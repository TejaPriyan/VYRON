"""The live GitHub provider: reads real repository data from the GitHub REST API at request time.

It composes the pieces in this package and adds no behaviour of its own beyond checking what came back:

    transport (urllib, fixed host, no redirects) -> retry policy -> conditional cache -> normaliser

Nothing here is verified against the real GitHub API from the build environment, which has no GitHub access
(see docs/GITHUB.md). What *is* verified is every layer against a local server that speaks GitHub's documented
response shapes, and ``scripts/live_github_check.py`` exists so the first real run can be done, and its raw
responses captured as fixtures, on a machine that can reach GitHub.

Interactive defaults are deliberately short (one retry, a few seconds of waiting): a person is watching the
page, and the development server handles one request at a time. A longer wait is reported, not slept through.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol

from vyron_api.adapters.memory_cache import InMemoryTTLCache
from vyron_api.github.budget import RateLimitBudget
from vyron_api.github.conditional import CachedResult, cache_key, cached_get
from vyron_api.github.normalize import (
    TOP_CONTRIBUTORS,
    MalformedGitHubPayloadError,
    normalize_commit_activity,
    normalize_contributors,
    normalize_languages,
    normalize_rate_limit,
    normalize_release,
    normalize_repository,
)
from vyron_api.github.ratelimit import RateLimitState
from vyron_api.github.retry import RawResponse, Sleeper, send_with_retry
from vyron_api.github.transport import DEFAULT_BASE_URL, UrllibGitHubTransport
from vyron_api.github.url import InvalidRepoReferenceError, RepoRef, parse_repo_reference
from vyron_api.providers.cache import CacheProvider
from vyron_api.providers.github import (
    CacheStatus,
    CommitActivityResult,
    CommitActivityStatus,
    Contributor,
    ContributorsStatus,
    GitHubForbiddenError,
    GitHubNotFoundError,
    LatestRelease,
    RepoActivityResult,
    RepoAnalysisResult,
    RepoSnapshot,
    WeekOfCommits,
)

#: A copy confirmed this recently is served without asking GitHub again. Matches the route's max-age.
FRESH_FOR_SECONDS = 60.0
#: A stale copy is only used when GitHub cannot be reached; beyond a day it is not offered at all.
STALE_TTL_SECONDS = 86_400.0


class GitHubGet(Protocol):
    """What the provider needs from a transport; ``UrllibGitHubTransport`` is the real one."""

    def url_for(self, path_or_url: str) -> str: ...

    async def get(self, path_or_url: str, extra_headers: Mapping[str, str] | None = None) -> RawResponse: ...


def _utc(epoch_seconds: float) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, UTC)


def _overall_status(reads: Sequence[CachedResult], *, uncached_answers: int = 0) -> CacheStatus:
    """How current the whole answer is, claiming only what is true of every part of it.

    ``stale``: any part is an older copy because GitHub could not be asked. ``revalidated`` means "nothing had
    changed since the previous read", so it is claimed only when every part came from a stored copy and at least
    one was just confirmed unchanged (a 304). Any part read from GitHub just now, new data or an answer that is
    never stored (``uncached_answers``: a 403 or a 404 that is reported rather than raised), makes it ``fresh``.
    The first real run showed why: a repository copy confirmed by a 304 next to a contributor list and a release
    read for the first time was labelled "nothing had changed", which was true of one part only.
    """
    if any(read.stale for read in reads):
        return CacheStatus.STALE
    everything_stored = uncached_answers == 0 and all(read.from_cache for read in reads)
    if everything_stored and any(read.revalidated for read in reads):
        return CacheStatus.REVALIDATED
    return CacheStatus.FRESH


class LiveGitHubProvider:
    def __init__(
        self,
        transport: GitHubGet,
        cache: CacheProvider,
        *,
        budget: RateLimitBudget | None = None,
        fresh_for_seconds: float = FRESH_FOR_SECONDS,
        ttl_seconds: float = STALE_TTL_SECONDS,
        max_retries: int = 1,
        max_wait_seconds: float = 3.0,
        base_backoff: float = 0.5,
        clock: Callable[[], float] = time.time,
        sleep: Sleeper = asyncio.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0.0, 0.25),
    ) -> None:
        self._transport = transport
        self._cache = cache
        self._budget = budget if budget is not None else RateLimitBudget(now=clock)
        self._fresh_for = fresh_for_seconds
        self._ttl = ttl_seconds
        self._clock = clock
        self._retry = {
            "max_retries": max_retries,
            "max_wait_seconds": max_wait_seconds,
            "base_backoff": base_backoff,
            "sleep": sleep,
            "jitter": jitter,
        }

    # ---- the protocol -------------------------------------------------------------------------
    async def get_repository(self, ref: RepoRef) -> RepoSnapshot:
        snapshot, _ = await self._repository(ref)
        return snapshot

    async def get_languages(self, ref: RepoRef) -> Mapping[str, int]:
        languages, _ = await self._languages(ref)
        return languages

    async def analyze_repository(self, ref: RepoRef) -> RepoAnalysisResult:
        snapshot, repo_read = await self._repository(ref)  # a missing repository stops here, before a second call
        languages, language_read = await self._languages(ref)
        reads = (repo_read, language_read)
        return RepoAnalysisResult(
            snapshot=snapshot,
            languages=languages,
            cache_status=_overall_status(reads),
            fetched_at=_utc(min(read.as_of for read in reads)),
            source_urls=(
                self._transport.url_for(ref.api_path),
                self._transport.url_for(f"{ref.api_path}/languages"),
            ),
        )

    async def repository_activity(self, ref: RepoRef) -> RepoActivityResult:
        """Top contributors and the latest release: the opt-in second read, never part of the basic analysis.

        The repository is read first, for the same reasons as in the basic analysis (a private repository is
        refused before anything else is asked, and GitHub's spelling of the name is checked). That read comes
        from the cache when it is under a minute old, so this costs two requests, or three when it is not.

        Two answers are reported rather than raised, because GitHub gives them for ordinary repositories:
        403 on the contributor list (documented for very history-heavy repositories) becomes ``declined``, and
        404 on "latest release" (no published release) becomes ``None``. Neither is cached: an answer that is
        not a 200 is asked again next time, as everywhere in this module. Every other failure propagates.
        """
        snapshot, repo_read = await self._repository(ref)
        canonical = parse_repo_reference(snapshot.full_name)  # already checked to be ``ref`` by ``_repository``
        reads: list[CachedResult] = [repo_read]
        as_of: list[float] = [repo_read.as_of]
        reported_answers = 0  # a 403 or a 404 read just now and never stored
        contributors_path = f"{ref.api_path}/contributors?per_page={TOP_CONTRIBUTORS}"
        release_path = f"{ref.api_path}/releases/latest"

        contributors: tuple[Contributor, ...] = ()
        status = ContributorsStatus.LISTED
        try:
            read = await self._read(contributors_path)
        except GitHubForbiddenError:
            status = ContributorsStatus.DECLINED
            reported_answers += 1
            as_of.append(self._clock())
        else:
            reads.append(read)
            as_of.append(read.as_of)
            if read.response.status == 204:
                status = ContributorsStatus.NONE
            else:
                contributors = normalize_contributors(read.response.json())
                if not contributors:
                    status = ContributorsStatus.NONE

        latest: LatestRelease | None = None
        try:
            release_read = await self._read(release_path)
        except GitHubNotFoundError:
            reported_answers += 1
            as_of.append(self._clock())
        else:
            reads.append(release_read)
            as_of.append(release_read.as_of)
            latest = normalize_release(release_read.response.json(), canonical)

        return RepoActivityResult(
            full_name=snapshot.full_name,
            contributors_status=status,
            contributors=contributors,
            latest_release=latest,
            cache_status=_overall_status(reads, uncached_answers=reported_answers),
            fetched_at=_utc(min(as_of)),
            source_urls=(
                self._transport.url_for(ref.api_path),
                self._transport.url_for(contributors_path),
                self._transport.url_for(release_path),
            ),
        )

    async def commit_activity(self, ref: RepoRef) -> CommitActivityResult:
        """A year of weekly commit counts: the opt-in read that GitHub computes in the background.

        GitHub does not compute these on request. The first ask for a repository it has not computed recently is
        answered ``202`` with an empty body while it works (real runs, 2026-11-04: ``pallets/flask`` answered 202
        three times over eleven seconds and 200 on the fourth ask, at 16 seconds; ``torvalds/linux`` answered 200
        at once). This method never waits for it. A 202 is reported as ``computing`` and the reader asks again
        when they choose to, because every ask, a 202 included, costs a request from a small quota.

        The repository is read first, for the same reasons as everywhere here (a private repository is refused
        before anything else is asked, and GitHub's spelling of the name is checked). That read comes from the
        cache when it is under a minute old, so this costs one more request, or two when it is not.

        Only a 200 is stored. A 202 or a 204 is reported and asked again next time. An answer that is not a
        list of weeks in the documented shape raises ``MalformedGitHubPayloadError``; every other failure
        propagates as it does for the other reads.
        """
        snapshot, repo_read = await self._repository(ref)
        path = f"{ref.api_path}/stats/commit_activity"
        read = await self._read(path)
        status = CommitActivityStatus.READY
        weeks: tuple[WeekOfCommits, ...] = ()
        if read.response.status == 202:
            status = CommitActivityStatus.COMPUTING
        elif read.response.status == 204:
            status = CommitActivityStatus.NONE
        else:
            weeks = normalize_commit_activity(read.response.json())
            if not weeks:
                status = CommitActivityStatus.NONE
        # A 202 or a 204 is read from GitHub just now and never stored, so ``read`` itself is not "from cache" and the
        # whole answer can never be called "confirmed unchanged" (``_overall_status``).
        return CommitActivityResult(
            full_name=snapshot.full_name,
            status=status,
            weeks=weeks,
            cache_status=_overall_status((repo_read, read)),
            fetched_at=_utc(min(repo_read.as_of, read.as_of)),
            source_urls=(self._transport.url_for(ref.api_path), self._transport.url_for(path)),
        )

    async def rate_limit_status(self) -> RateLimitState:
        """The quota as of the last response if the window is still open, else a live (free) ask."""
        known = self._budget.snapshot().get("core")
        if known is not None and known.remaining is not None and known.reset_epoch is not None:
            if known.reset_epoch > self._clock():
                return known

        async def send(headers: Mapping[str, str]) -> RawResponse:
            # Asking for the quota is free and its headers are not used to update the budget.
            return await self._send("/rate_limit", headers, counted=False)

        response = await send_with_retry(send, now=self._clock, **self._retry)  # type: ignore[arg-type]
        return normalize_rate_limit(response.json())

    # ---- reads --------------------------------------------------------------------------------
    async def _repository(self, ref: RepoRef) -> tuple[RepoSnapshot, CachedResult]:
        read = await self._read(ref.api_path)
        payload = read.response.json()
        # The route is for public repositories. A server token that can see private ones must not turn it into a
        # way to read them, so anything not explicitly public is treated as absent. (Cached bodies are re-checked.)
        if not isinstance(payload, Mapping) or payload.get("private") is not False:
            raise GitHubNotFoundError("repository is not public")
        snapshot = normalize_repository(payload, _utc(read.as_of))
        try:
            canonical = parse_repo_reference(snapshot.full_name)
        except InvalidRepoReferenceError as exc:
            raise MalformedGitHubPayloadError("'full_name' is not a valid repository name") from exc
        if canonical.key != ref.key:
            raise MalformedGitHubPayloadError("GitHub answered with a different repository than was requested")
        return snapshot, read

    async def _languages(self, ref: RepoRef) -> tuple[Mapping[str, int], CachedResult]:
        read = await self._read(f"{ref.api_path}/languages")
        return normalize_languages(read.response.json()), read

    async def _read(self, path: str) -> CachedResult:
        return await cached_get(
            self._cache,
            cache_key(path),
            lambda headers: self._send(path, headers, counted=True),
            ttl_seconds=self._ttl,
            fresh_for_seconds=self._fresh_for,
            now=self._clock,
            **self._retry,
        )

    async def _send(self, path: str, headers: Mapping[str, str], *, counted: bool) -> RawResponse:
        if counted:
            self._budget.check("core")  # refuse early rather than spend the last of the quota
        response = await self._transport.get(path, headers)
        if counted:
            self._budget.update(response.headers)
        return response


def build_live_provider(
    *,
    token: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    allow_local_test_server: bool = False,
    timeout: float = 10.0,
    cache: CacheProvider | None = None,
) -> LiveGitHubProvider:
    """The provider for real use: ``https://api.github.com``, an optional server-side token, an in-memory cache.

    ``allow_local_test_server`` lets ``base_url`` be a loopback address, for tests that stand in for GitHub. It is
    never enabled by configuration read from the environment.
    """
    transport = UrllibGitHubTransport(
        token=token, base_url=base_url, timeout=timeout, allow_local_test_server=allow_local_test_server
    )
    return LiveGitHubProvider(transport, cache if cache is not None else InMemoryTTLCache())
