"""Normalize GitHub REST payloads into VYRON snapshots, treating the payload as untrusted.

Field names were checked against GitHub's "Get a repository" response schema (2026-10-05):
``full_name``, ``description`` (nullable), ``default_branch``, ``language`` (nullable), ``topics``,
``license`` (nullable object with ``spdx_id``), ``stargazers_count``, ``forks_count``,
``open_issues_count`` (GitHub counts open pull requests in this number), ``archived``, ``fork``,
``created_at``, ``pushed_at``. Nothing is coerced: a wrong type raises instead of being guessed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from vyron_api.github.ratelimit import RateLimitState
from vyron_api.github.url import RepoRef, is_account_name
from vyron_api.providers.github import Contributor, LatestRelease, RepoSnapshot, WeekOfCommits

MAX_DESCRIPTION_CHARS = 2000
MAX_TOPICS = 100
MAX_LANGUAGES = 200
MAX_CONTRIBUTOR_ENTRIES = 100  # GitHub's page-size ceiling; more than this in one answer is not a page
TOP_CONTRIBUTORS = 10
MAX_WEEKS = 60  # GitHub documents the last year: 52 weeks. Far more than a year is not this answer
_SECONDS_PER_DAY = 86_400
_NAME_MAX = 200
_TAG_MAX = 255
# A login as GitHub shows it: letters, digits, hyphens, and for some accounts underscores or a "[bot]" suffix.
# Wider than the owner rule in url.py on purpose (every account GitHub lists must be displayable); only logins
# that also pass that stricter rule are linked.
_LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,38}(?:\[bot\])?$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_RELEASE_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/releases/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$"
)


class MalformedGitHubPayloadError(ValueError):
    """GitHub returned data that does not match the documented shape."""


def _str(payload: Mapping[str, Any], key: str, *, max_chars: int = _NAME_MAX) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_chars:
        raise MalformedGitHubPayloadError(f"{key!r} must be a non-empty string")
    return value


def _opt_str(payload: Mapping[str, Any], key: str, *, max_chars: int) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedGitHubPayloadError(f"{key!r} must be a string or null")
    value = value.strip()
    return value[:max_chars] or None


def _count(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MalformedGitHubPayloadError(f"{key!r} must be a non-negative integer")
    return value


def _bool(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise MalformedGitHubPayloadError(f"{key!r} must be a boolean")
    return value


def parse_timestamp(value: Any, key: str) -> datetime | None:
    """Parse GitHub's ISO 8601 timestamps (``...Z``) into aware UTC datetimes."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedGitHubPayloadError(f"{key!r} must be a timestamp string or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MalformedGitHubPayloadError(f"{key!r} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise MalformedGitHubPayloadError(f"{key!r} must include a timezone")
    return parsed.astimezone(UTC)


def normalize_repository(payload: Any, fetched_at: datetime) -> RepoSnapshot:
    if not isinstance(payload, Mapping):
        raise MalformedGitHubPayloadError("repository payload must be an object")
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")

    topics_raw = payload.get("topics", [])
    if (
        not isinstance(topics_raw, list)
        or len(topics_raw) > MAX_TOPICS
        or not all(isinstance(t, str) and 0 < len(t) <= 100 for t in topics_raw)
    ):
        raise MalformedGitHubPayloadError("'topics' must be a list of short strings")

    license_raw = payload.get("license")
    license_spdx: str | None = None
    license_name: str | None = None
    if license_raw is not None:
        if not isinstance(license_raw, Mapping):
            raise MalformedGitHubPayloadError("'license' must be an object or null")
        spdx = license_raw.get("spdx_id")
        if spdx is not None and not isinstance(spdx, str):
            raise MalformedGitHubPayloadError("'license.spdx_id' must be a string or null")
        license_spdx = None if spdx in (None, "", "NOASSERTION") else spdx[:64]
        license_name = _opt_str(license_raw, "name", max_chars=100)

    return RepoSnapshot(
        full_name=_str(payload, "full_name"),
        description=_opt_str(payload, "description", max_chars=MAX_DESCRIPTION_CHARS),
        default_branch=_str(payload, "default_branch"),
        primary_language=_opt_str(payload, "language", max_chars=_NAME_MAX),
        topics=tuple(topics_raw),
        license_spdx=license_spdx,
        stars=_count(payload, "stargazers_count"),
        forks=_count(payload, "forks_count"),
        open_issues=_count(payload, "open_issues_count"),
        archived=_bool(payload, "archived"),
        is_fork=_bool(payload, "fork"),
        created_at=parse_timestamp(payload.get("created_at"), "created_at"),
        pushed_at=parse_timestamp(payload.get("pushed_at"), "pushed_at"),
        fetched_at=fetched_at,
        license_name=license_name,
    )


def normalize_rate_limit(payload: Any, resource: str = "core") -> RateLimitState:
    """``GET /rate_limit``: ``{"resources": {"core": {"limit", "used", "remaining", "reset"}, ...}}``.

    Asking GitHub for this does not count against the quota it reports. Counts must be non-negative integers.
    """
    if not isinstance(payload, Mapping):
        raise MalformedGitHubPayloadError("rate limit payload must be an object")
    resources = payload.get("resources")
    if not isinstance(resources, Mapping):
        raise MalformedGitHubPayloadError("'resources' must be an object")
    entry = resources.get(resource)
    if not isinstance(entry, Mapping):
        raise MalformedGitHubPayloadError(f"rate limit for {resource!r} is missing")
    return RateLimitState(
        limit=_count(entry, "limit"),
        remaining=_count(entry, "remaining"),
        used=_count(entry, "used"),
        reset_epoch=_count(entry, "reset"),
        resource=resource,
    )


def normalize_languages(payload: Any) -> dict[str, int]:
    """``GET /repos/{owner}/{repo}/languages`` returns ``{language: bytes}``."""
    if not isinstance(payload, Mapping):
        raise MalformedGitHubPayloadError("languages payload must be an object")
    if len(payload) > MAX_LANGUAGES:
        raise MalformedGitHubPayloadError("languages payload is unexpectedly large")
    result: dict[str, int] = {}
    for name, size in payload.items():
        if not isinstance(name, str) or not name or len(name) > _NAME_MAX:
            raise MalformedGitHubPayloadError("language names must be short strings")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise MalformedGitHubPayloadError(f"language size for {name!r} must be a byte count")
        result[name] = size
    return result


def normalize_contributors(payload: Any, limit: int = TOP_CONTRIBUTORS) -> tuple[Contributor, ...]:
    """``GET /repos/{owner}/{repo}/contributors``: a list of accounts with a ``contributions`` count.

    Only ``login``, ``type`` and ``contributions`` are read. ``avatar_url``, ``html_url`` and the rest are
    ignored: a link is built from a login that passes GitHub's naming rules, and no image is ever loaded from
    a payload. An ``Anonymous`` entry (GitHub only sends them when asked with ``anon=1``, which VYRON never
    does) has an email address instead of a login and is skipped, never kept. The result is ordered by
    ``contributions`` (largest first, GitHub's own order kept among equals) and cut to ``limit``.
    """
    if not isinstance(payload, list):
        raise MalformedGitHubPayloadError("contributors payload must be a list")
    if len(payload) > MAX_CONTRIBUTOR_ENTRIES:
        raise MalformedGitHubPayloadError("contributors payload is unexpectedly large")
    found: list[Contributor] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise MalformedGitHubPayloadError("each contributor must be an object")
        kind = item.get("type")
        if not isinstance(kind, str):
            raise MalformedGitHubPayloadError("'type' must be a string")
        if kind == "Anonymous":
            continue
        login = item.get("login")
        if not isinstance(login, str) or not _LOGIN_RE.fullmatch(login):
            raise MalformedGitHubPayloadError("'login' is not a valid account name")
        is_bot = kind == "Bot"
        linkable = not is_bot and is_account_name(login)
        found.append(
            Contributor(
                login=login,
                contributions=_count(item, "contributions"),
                is_bot=is_bot,
                profile_url=f"https://github.com/{login}" if linkable else None,
            )
        )
    found.sort(key=lambda contributor: -contributor.contributions)  # stable: ties keep GitHub's order
    return tuple(found[:limit])


def normalize_release(payload: Any, repo: RepoRef) -> LatestRelease:
    """``GET /repos/{owner}/{repo}/releases/latest``. Release notes, assets and author are not read.

    ``repo`` is the repository this was read for (GitHub's canonical spelling). GitHub's ``html_url`` is used
    only when it is a ``github.com`` page under that repository's ``/releases/``; otherwise the link falls
    back to the repository's releases page. A draft is refused: "latest" never is one, so seeing one means the
    answer is not what was documented.
    """
    if not isinstance(payload, Mapping):
        raise MalformedGitHubPayloadError("release payload must be an object")
    if _bool(payload, "draft"):
        raise MalformedGitHubPayloadError("the latest release must not be a draft")
    tag = _str(payload, "tag_name", max_chars=_TAG_MAX)
    if _CONTROL_RE.search(tag):
        raise MalformedGitHubPayloadError("'tag_name' contains control characters")
    name = _opt_str(payload, "name", max_chars=_NAME_MAX)
    if name is not None:
        name = _CONTROL_RE.sub(" ", name).strip() or None
    url = payload.get("html_url")
    prefix = f"https://github.com/{repo.owner}/{repo.repo}/releases/".lower()
    page_ok = (
        isinstance(url, str)
        and len(url) <= 300
        and url.lower().startswith(prefix)
        and bool(_RELEASE_URL_RE.fullmatch(url))
    )
    return LatestRelease(
        tag_name=tag,
        name=name,
        published_at=parse_timestamp(payload.get("published_at"), "published_at"),
        prerelease=_bool(payload, "prerelease"),
        html_url=url if page_ok and isinstance(url, str) else f"{repo.html_url}/releases",
    )


def normalize_commit_activity(payload: Any) -> tuple[WeekOfCommits, ...]:
    """``GET /repos/{owner}/{repo}/stats/commit_activity``: a year of weekly counts, oldest week first.

    Each entry is ``{"days": [seven counts, Sunday first], "total": n, "week": unix seconds}``. The shape was
    checked against two real answers (``pallets/flask`` and ``torvalds/linux``, 2026-11-04): 52 entries, the
    ``week`` values exactly one week apart and each a Sunday at 00:00 UTC, and every ``total`` equal to the sum
    of its ``days``. Those are held to here rather than assumed, because a chart drawn from a payload that
    broke them would say something GitHub did not. Anything else raises; nothing is repaired or coerced.
    An empty list is "no weeks to show" and returns an empty tuple.
    """
    if not isinstance(payload, list):
        raise MalformedGitHubPayloadError("commit activity payload must be a list")
    if len(payload) > MAX_WEEKS:
        raise MalformedGitHubPayloadError("commit activity payload has more weeks than a year")
    weeks: list[WeekOfCommits] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise MalformedGitHubPayloadError("each week must be an object")
        days = item.get("days")
        if not isinstance(days, list) or len(days) != 7:
            raise MalformedGitHubPayloadError("'days' must be a list of seven counts")
        counts: list[int] = []
        for day in days:
            if isinstance(day, bool) or not isinstance(day, int) or day < 0:
                raise MalformedGitHubPayloadError("each day's commits must be a non-negative integer")
            counts.append(day)
        total = _count(item, "total")
        if total != sum(counts):
            raise MalformedGitHubPayloadError("a week's 'total' does not equal the sum of its days")
        epoch = _count(item, "week")
        try:
            start = datetime.fromtimestamp(epoch, UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise MalformedGitHubPayloadError("'week' is not a valid timestamp") from exc
        if start.weekday() != 6 or epoch % _SECONDS_PER_DAY != 0:
            raise MalformedGitHubPayloadError("'week' must be a Sunday at 00:00 UTC")
        if weeks and (epoch - int(weeks[-1].week_start.timestamp())) != 7 * _SECONDS_PER_DAY:
            raise MalformedGitHubPayloadError("weeks must be consecutive, oldest first")
        weeks.append(WeekOfCommits(week_start=start, days=tuple(counts), total=total))
    return tuple(weeks)
