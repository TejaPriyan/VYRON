from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from vyron_api.github.ratelimit import RateLimitState
from vyron_api.github.url import RepoRef


class GitHubError(Exception):
    """Base class for GitHub provider failures."""


class GitHubNotFoundError(GitHubError):
    """The repository does not exist or is not visible to the configured credentials."""


class GitHubAuthError(GitHubError):
    """The configured token is missing, expired or revoked (HTTP 401)."""


class GitHubForbiddenError(GitHubError):
    """Access denied for a reason other than rate limiting (plain HTTP 403)."""


class GitHubValidationError(GitHubError):
    """GitHub rejected the request as invalid (HTTP 422 or 400)."""


class GitHubMovedError(GitHubError):
    """The repository was renamed or transferred (HTTP 301, 302, 307 or 308). Redirects are never followed."""


class GitHubUnavailableError(GitHubError):
    """GitHub could not be reached or kept failing; serve cached data if there is any."""


class GitHubRateLimitedError(GitHubError):
    def __init__(self, message: str, wait_seconds: float) -> None:
        super().__init__(message)
        self.wait_seconds = wait_seconds


@dataclass(frozen=True, slots=True)
class RepoSnapshot:
    """Normalized subset of GitHub's repository resource. Values are as reported at fetch time."""

    full_name: str
    description: str | None
    default_branch: str
    primary_language: str | None
    topics: Sequence[str]
    license_spdx: str | None
    stars: int
    forks: int
    open_issues: int
    archived: bool
    is_fork: bool
    created_at: datetime | None
    pushed_at: datetime | None
    fetched_at: datetime
    #: GitHub's own name for the licence it found, as it reported it (for example "MIT License" or "Other").
    #: Kept beside ``license_spdx`` because GitHub can find a licence file and still give it no SPDX identifier
    #: (``spdx_id`` is "NOASSERTION", ``license_spdx`` is None, and this is "Other": torvalds/linux, 2026-11-02).
    license_name: str | None = None


class CacheStatus(StrEnum):
    """How current a repository read is. Always shown with ``fetched_at`` so a reader can judge it."""

    FRESH = "fresh"  # read from GitHub just now, or from a copy confirmed within the freshness window
    REVALIDATED = "revalidated"  # a conditional request just confirmed the cached copy is unchanged
    STALE = "stale"  # GitHub could not be reached or was rate limiting, so an older copy is shown


@dataclass(frozen=True, slots=True)
class RepoAnalysisResult:
    """One repository read: what GitHub reported, when, through which cache path, and from which URLs."""

    snapshot: RepoSnapshot
    languages: Mapping[str, int]
    cache_status: CacheStatus
    #: The older of the two reads' "as of" times, so the whole result is never presented as newer than its parts.
    fetched_at: datetime
    #: The exact GitHub API URLs the values were read from: the evidence for a ``sourced`` label.
    source_urls: Sequence[str]


class ContributorsStatus(StrEnum):
    """What GitHub said about a repository's contributors. Never guessed: it is one of three answers."""

    LISTED = "listed"  # GitHub returned a list (up to the number asked for)
    NONE = "none"  # GitHub answered 204 No Content: it has no contributors to list (an empty repository)
    DECLINED = "declined"  # GitHub refused to list them (documented for very large histories); not an error


@dataclass(frozen=True, slots=True)
class Contributor:
    """One account GitHub lists as a contributor. Only these four values are kept; email and avatar are not."""

    login: str
    #: Commits GitHub attributes to the account, as it reports them (it groups by author email address).
    contributions: int
    is_bot: bool
    #: Built by VYRON from a login that passes GitHub's naming rules, never copied from the payload. None for
    #: bots and for logins that do not pass those rules.
    profile_url: str | None


@dataclass(frozen=True, slots=True)
class LatestRelease:
    """GitHub's "latest release": the most recent release that is not a draft or a pre-release.

    GitHub's documentation (read 2026-11-02) orders them by ``created_at``, which it says is the date of the commit
    used for the release, not of drafting or publishing. That is GitHub's definition. It is not necessarily the
    highest version number. Release notes are not read.
    """

    tag_name: str
    name: str | None
    published_at: datetime | None
    prerelease: bool
    #: The release's page on github.com when GitHub's own ``html_url`` is one; otherwise the repository's
    #: releases page, so a link never points anywhere but this repository's releases.
    html_url: str


@dataclass(frozen=True, slots=True)
class RepoActivityResult:
    """Top contributors and the latest release of one public repository, with where and when they were read."""

    full_name: str
    contributors_status: ContributorsStatus
    contributors: Sequence[Contributor]
    #: None when GitHub answered 404 to "latest release" (the repository has no published release).
    latest_release: LatestRelease | None
    cache_status: CacheStatus
    #: The oldest "as of" time among the reads, so the whole result is never presented as newer than its parts.
    fetched_at: datetime
    #: Every GitHub API URL whose answer the values rest on, including a 204, a 403 or a 404 that was reported.
    source_urls: Sequence[str]


class CommitActivityStatus(StrEnum):
    """What GitHub said when asked for a year of weekly commit counts. One of three answers, never guessed."""

    READY = "ready"  # GitHub returned the weekly counts
    COMPUTING = "computing"  # GitHub answered 202: it is still working them out; asking again later may work
    NONE = "none"  # GitHub answered 204 (or an empty list): it has no commit activity to report


@dataclass(frozen=True, slots=True)
class WeekOfCommits:
    """One week of commits on the default branch, as GitHub reports it. Nothing here is estimated or smoothed."""

    #: The Sunday, 00:00 UTC, that starts the week.
    week_start: datetime
    #: Commits on each day of the week, Sunday first. Always seven values.
    days: Sequence[int]
    #: GitHub's own total for the week. It is checked to equal the sum of ``days`` before it is kept.
    total: int


@dataclass(frozen=True, slots=True)
class CommitActivityResult:
    """The last year of commits, week by week, for one public repository, with where and when it was read.

    ``weeks`` is empty unless ``status`` is ``READY``. GitHub computes this in the background, so the first ask
    for a repository it has not computed recently can only be answered "computing".
    """

    full_name: str
    status: CommitActivityStatus
    weeks: Sequence[WeekOfCommits]
    cache_status: CacheStatus
    #: The oldest "as of" time among the reads, so the whole result is never presented as newer than its parts.
    fetched_at: datetime
    #: Every GitHub API URL whose answer the values rest on, including a 202 or a 204 that was reported.
    source_urls: Sequence[str]


class GitHubProvider(Protocol):
    async def get_repository(self, ref: RepoRef) -> RepoSnapshot: ...

    async def get_languages(self, ref: RepoRef) -> Mapping[str, int]: ...

    async def analyze_repository(self, ref: RepoRef) -> RepoAnalysisResult: ...

    async def repository_activity(self, ref: RepoRef) -> RepoActivityResult: ...

    async def commit_activity(self, ref: RepoRef) -> CommitActivityResult: ...

    async def rate_limit_status(self) -> RateLimitState: ...
