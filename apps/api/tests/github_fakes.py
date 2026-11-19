"""Test doubles and payloads for the live GitHub path.

EVERYTHING HERE IS A FIXTURE WRITTEN FROM GITHUB'S DOCUMENTATION, NOT CAPTURED FROM GITHUB. The repository is
a made-up one (``fixture-org/example-repo``). The payloads include fields VYRON ignores, because a real
response has many more fields than the normaliser reads. Real responses are in ``fixtures/github_real`` and are
exercised by ``test_github_real_captures.py``. Where these hand-written ones differ from the real ones: ETags are
strong here and weak (``W/"..."``) in reality, there is no ``last-modified``, a 404 body is only a message (GitHub
adds ``documentation_url`` and a string ``status``), and a 301 here carries a ``location`` header the real capture
did not record (its target is in the body's ``url``). The contributors and latest-release payloads below were
written before any real answer had been seen. The real ones (``pallets_flask_activity``, captured 2026-11-02)
differ the same way on ETags and ``last-modified``, and also in these: the contributors answer carries a ``link``
header (``rel="next"``, 40 pages), entries have fields such as ``user_view_type`` and ``gravatar_id``, bots'
``html_url`` is under ``github.com/apps/``, and a release's ``name`` can be the empty string, not null or text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from vyron_api.github.ratelimit import RateLimitState
from vyron_api.github.retry import RawResponse
from vyron_api.github.url import RepoRef
from vyron_api.providers.github import (
    CacheStatus,
    CommitActivityResult,
    CommitActivityStatus,
    Contributor,
    ContributorsStatus,
    LatestRelease,
    RepoActivityResult,
    RepoAnalysisResult,
    RepoSnapshot,
    WeekOfCommits,
)

FIXTURE_REF = RepoRef("fixture-org", "example-repo")
FIXTURE_FETCHED_AT = datetime(2026, 10, 21, 10, 15, 30, tzinfo=UTC)


def repo_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": 1296269,
        "node_id": "MDEwOlJlcG9zaXRvcnkxMjk2MjY5",
        "name": "example-repo",
        "full_name": "fixture-org/example-repo",
        "private": False,
        "owner": {"login": "fixture-org", "id": 1, "type": "Organization", "site_admin": False},
        "html_url": "https://github.com/fixture-org/example-repo",
        "description": "Fixture for tests. This is not data from GitHub.",
        "fork": False,
        "url": "https://api.github.com/repos/fixture-org/example-repo",
        "created_at": "2018-03-04T05:06:07Z",
        "updated_at": "2026-10-20T08:00:00Z",
        "pushed_at": "2026-10-20T07:30:00Z",
        "homepage": "https://example.invalid",
        "size": 108,
        "stargazers_count": 1234,
        "watchers_count": 1234,
        "language": "Python",
        "forks_count": 56,
        "open_issues_count": 7,
        "license": {
            "key": "mit", "name": "MIT License", "spdx_id": "MIT",
            "url": "https://api.github.com/licenses/mit", "node_id": "MDc6TGljZW5zZTEz",
        },  # fmt: skip
        "topics": ["fixture", "example"],
        "visibility": "public",
        "forks": 56,
        "open_issues": 7,
        "watchers": 1234,
        "default_branch": "main",
        "archived": False,
        "disabled": False,
        "has_issues": True,
    }
    payload.update(overrides)
    return payload


def languages_payload() -> dict[str, int]:
    return {"Python": 7000, "JavaScript": 2000, "Shell": 1000}


def contributors_payload() -> list[dict[str, Any]]:
    """Hand-written from GitHub's documented shape. Real entries also carry many URL fields VYRON ignores."""

    def entry(login: str, ident: int, kind: str, contributions: int, page: str) -> dict[str, Any]:
        return {
            "login": login, "id": ident, "type": kind, "site_admin": False, "contributions": contributions,
            "avatar_url": f"https://avatars.githubusercontent.com/u/{ident}?v=4",
            "html_url": f"https://github.com/{page}",
        }  # fmt: skip

    return [
        entry("fixture-person-a", 101, "User", 120, "fixture-person-a"),
        entry("fixture-person-b", 102, "User", 45, "fixture-person-b"),
        entry("fixture-helper[bot]", 103, "Bot", 30, "apps/fixture-helper"),
    ]


def release_payload(**overrides: Any) -> dict[str, Any]:
    """Hand-written from GitHub's documented shape ('Get the latest release'); real ones carry many more fields."""
    payload: dict[str, Any] = {
        "id": 555,
        "tag_name": "v1.2.3",
        "target_commitish": "main",
        "name": "Fixture release",
        "draft": False,
        "prerelease": False,
        "created_at": "2026-09-01T11:00:00Z",
        "published_at": "2026-09-01T12:00:00Z",
        "html_url": "https://github.com/fixture-org/example-repo/releases/tag/v1.2.3",
        "body": "Release notes are never read.",
        "assets": [],
    }
    payload.update(overrides)
    return payload


FIRST_FIXTURE_WEEK = 1_762_646_400  # Sunday 2025-11-09 00:00 UTC: where the real captures of 2026-11-04 begin


def commit_activity_payload(weeks: int = 52) -> list[dict[str, Any]]:
    """A year of weekly commit counts in the shape GitHub really sent on 2026-11-04 (see ``fixtures/github_real``).

    THE NUMBERS ARE MADE UP: a fixed, uneven pattern that rises a little over the year, busier midweek, with a
    quiet week (zero) every thirteenth, the seventh of each thirteen. The shape is not made up: 52 entries of
    ``{"days": [7 counts, Sunday first], "total": sum, "week": Sunday 00:00 UTC}``, oldest first. GitHub's
    documentation example uses week timestamps that are not midnight UTC; the real answers did not, and VYRON
    rejects a payload that does (``normalize_commit_activity``).
    """
    levels = (6, 14, 22, 9, 31, 17, 0, 26, 8, 19, 38, 11, 15)
    shares = (1, 3, 4, 4, 4, 3, 1)  # of every 20 commits, how many fall on each day, Sunday first
    result: list[dict[str, Any]] = []
    for index in range(weeks):
        quiet = index % 13 == 6
        level = 0 if quiet else levels[index % 13] + (index // 13) * 3
        days = [level * share // 20 for share in shares]
        days[3] += level - sum(days)  # whatever the rounding left goes to the Wednesday
        result.append({"days": days, "total": sum(days), "week": FIRST_FIXTURE_WEEK + index * 604_800})
    return result


def rate_limit_payload(limit: int = 60, remaining: int = 58, reset: int = 1_792_580_000) -> dict[str, Any]:
    core = {"limit": limit, "used": limit - remaining, "remaining": remaining, "reset": reset}
    return {"resources": {"core": core, "search": {**core, "limit": 10}}, "rate": core}


def ok(body: Any, **headers: str) -> RawResponse:
    """A 200 response carrying an ETag unless one is given, so the cache can store and revalidate it."""
    sent = {"etag": '"fixture-etag"', "x-ratelimit-limit": "60", "x-ratelimit-remaining": "59",
            "x-ratelimit-used": "1", "x-ratelimit-reset": "1792580000", "x-ratelimit-resource": "core"}  # fmt: skip
    sent.update(headers)
    return RawResponse(200, sent, json.dumps(body).encode())


def error(status: int, message: str = "", **headers: str) -> RawResponse:
    return RawResponse(status, dict(headers), json.dumps({"message": message}).encode())


class FakeTransport:
    """A scripted stand-in for ``UrllibGitHubTransport``: answers by path and records every request.

    A path maps to one response (repeated) or a list played in order (the last one repeats). An ``Exception``
    in the script is raised instead of returned.
    """

    base = "https://api.github.com"

    def __init__(self, script: Mapping[str, RawResponse | Exception | Sequence[RawResponse | Exception]]) -> None:
        self.script = {path: list(item) if isinstance(item, Sequence) else [item] for path, item in script.items()}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def url_for(self, path_or_url: str) -> str:
        return self.base + path_or_url

    async def get(self, path_or_url: str, extra_headers: Mapping[str, str] | None = None) -> RawResponse:
        self.calls.append((path_or_url, dict(extra_headers or {})))
        queue = self.script.get(path_or_url)
        if not queue:
            raise AssertionError(f"unexpected request to {path_or_url}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]


class FakeGitHubProvider:
    """An in-memory ``GitHubProvider`` for HTTP-level tests; can be told to raise instead of answering."""

    def __init__(
        self, *, fail_with: Exception | None = None, commit_status: CommitActivityStatus = CommitActivityStatus.READY
    ) -> None:
        self.fail_with = fail_with
        self.commit_status = commit_status
        self.requested: list[RepoRef] = []

    async def get_repository(self, ref: RepoRef) -> RepoSnapshot:
        return (await self.analyze_repository(ref)).snapshot

    async def get_languages(self, ref: RepoRef) -> Mapping[str, int]:
        return (await self.analyze_repository(ref)).languages

    async def analyze_repository(self, ref: RepoRef) -> RepoAnalysisResult:
        self.requested.append(ref)
        if self.fail_with is not None:
            raise self.fail_with
        snapshot = RepoSnapshot(
            full_name=ref.full_name,
            description="Fixture for tests. This is not data from GitHub.",
            default_branch="main",
            primary_language="Python",
            topics=("fixture", "example"),
            license_spdx="MIT",
            license_name="MIT License",
            stars=1234,
            forks=56,
            open_issues=7,
            archived=False,
            is_fork=False,
            created_at=datetime(2018, 3, 4, 5, 6, 7, tzinfo=UTC),
            pushed_at=datetime(2026, 10, 20, 7, 30, tzinfo=UTC),
            fetched_at=FIXTURE_FETCHED_AT,
        )
        return RepoAnalysisResult(
            snapshot=snapshot,
            languages=languages_payload(),
            cache_status=CacheStatus.FRESH,
            fetched_at=FIXTURE_FETCHED_AT,
            source_urls=(
                f"https://api.github.com{ref.api_path}",
                f"https://api.github.com{ref.api_path}/languages",
            ),
        )

    async def repository_activity(self, ref: RepoRef) -> RepoActivityResult:
        self.requested.append(ref)
        if self.fail_with is not None:
            raise self.fail_with
        return RepoActivityResult(
            full_name=ref.full_name,
            contributors_status=ContributorsStatus.LISTED,
            contributors=(
                Contributor("fixture-person-a", 120, False, "https://github.com/fixture-person-a"),
                Contributor("fixture-helper[bot]", 30, True, None),
            ),
            latest_release=LatestRelease(
                tag_name="v1.2.3",
                name="Fixture release",
                published_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
                prerelease=False,
                html_url=f"https://github.com/{ref.full_name}/releases/tag/v1.2.3",
            ),
            cache_status=CacheStatus.FRESH,
            fetched_at=FIXTURE_FETCHED_AT,
            source_urls=(
                f"https://api.github.com{ref.api_path}",
                f"https://api.github.com{ref.api_path}/contributors?per_page=10",
                f"https://api.github.com{ref.api_path}/releases/latest",
            ),
        )

    async def commit_activity(self, ref: RepoRef) -> CommitActivityResult:
        self.requested.append(ref)
        if self.fail_with is not None:
            raise self.fail_with
        ready = self.commit_status is CommitActivityStatus.READY
        weeks = tuple(
            WeekOfCommits(datetime.fromtimestamp(item["week"], UTC), tuple(item["days"]), item["total"])
            for item in commit_activity_payload()
        )
        return CommitActivityResult(
            full_name=ref.full_name,
            status=self.commit_status,
            weeks=weeks if ready else (),
            cache_status=CacheStatus.FRESH,
            fetched_at=FIXTURE_FETCHED_AT,
            source_urls=(
                f"https://api.github.com{ref.api_path}",
                f"https://api.github.com{ref.api_path}/stats/commit_activity",
            ),
        )

    async def rate_limit_status(self) -> RateLimitState:
        if self.fail_with is not None:
            raise self.fail_with
        return RateLimitState(limit=60, remaining=58, used=2, reset_epoch=1_792_580_000, resource="core")


__all__ = [
    "FIRST_FIXTURE_WEEK",
    "FIXTURE_FETCHED_AT",
    "FIXTURE_REF",
    "FakeGitHubProvider",
    "FakeTransport",
    "commit_activity_payload",
    "contributors_payload",
    "error",
    "languages_payload",
    "ok",
    "rate_limit_payload",
    "release_payload",
    "repo_payload",
]
