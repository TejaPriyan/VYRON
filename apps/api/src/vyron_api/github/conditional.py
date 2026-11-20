"""Cached GET with ETag / Last-Modified revalidation and stale-on-error.

Store the ``etag`` and send it back as ``if-none-match`` (likewise ``last-modified`` /
``if-modified-since``); GitHub answers ``304 Not Modified`` with no body when nothing changed. I once wrote
here that a 304 does not count against the primary rate limit. A reported real run without a token (2026-10-28)
measured the opposite: each 304 cost one request (docs/GITHUB.md, "Does a 304 cost a request?"). With a token
it has not been measured. Nothing in this module depends on the answer: the quota shown to people comes from
GitHub's response headers. The cache has three behaviors: serve a *fresh* entry without calling GitHub
at all, revalidate an older one, and fall back to a *stale* entry when GitHub is down or rate limited.
Entries live in any ``CacheProvider`` (Redis in production).
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from vyron_api.github.retry import RawResponse, Sender, send_with_retry
from vyron_api.providers.cache import CacheProvider
from vyron_api.providers.github import GitHubError, GitHubRateLimitedError, GitHubUnavailableError

MAX_CACHED_BODY_BYTES = 2 * 1024 * 1024
ENTRY_VERSION = 1


@dataclass(frozen=True, slots=True)
class CacheEntry:
    etag: str | None
    last_modified: str | None
    body: bytes
    stored_at: float


@dataclass(frozen=True, slots=True)
class CachedResult:
    response: RawResponse
    #: Epoch seconds at which GitHub last confirmed this body: now for a network read or a 304, the stored
    #: time for a copy served from the freshness window or as a stale fallback. Never later than the truth.
    as_of: float
    from_cache: bool = False
    stale: bool = False
    #: True only when a conditional request just confirmed the cached body is unchanged (HTTP 304).
    revalidated: bool = False


def cache_key(path: str) -> str:
    """Stable, bounded key for a request path (including its query string)."""
    return "gh:v1:" + hashlib.sha256(path.encode()).hexdigest()


def encode_entry(entry: CacheEntry) -> bytes:
    return json.dumps(
        {
            "v": ENTRY_VERSION,
            "etag": entry.etag,
            "last_modified": entry.last_modified,
            "stored_at": entry.stored_at,
            "body": base64.b64encode(entry.body).decode("ascii"),
        }
    ).encode()


def decode_entry(data: bytes | None) -> CacheEntry | None:
    """Return the entry, or None if absent or corrupt (a corrupt entry is treated as a miss)."""
    if data is None:
        return None
    try:
        raw: Any = json.loads(data)
        if not isinstance(raw, dict) or raw.get("v") != ENTRY_VERSION:
            return None
        etag, last_modified = raw.get("etag"), raw.get("last_modified")
        stored_at = raw.get("stored_at")
        if etag is not None and not isinstance(etag, str):
            return None
        if last_modified is not None and not isinstance(last_modified, str):
            return None
        if isinstance(stored_at, bool) or not isinstance(stored_at, int | float):
            return None
        body = base64.b64decode(raw["body"], validate=True)
    except (ValueError, KeyError, TypeError):
        return None
    return CacheEntry(etag, last_modified, body, float(stored_at))


def conditional_headers(entry: CacheEntry | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if entry is not None:
        if entry.etag:
            headers["if-none-match"] = entry.etag
        if entry.last_modified:
            headers["if-modified-since"] = entry.last_modified
    return headers


def _from_entry(entry: CacheEntry, headers: Mapping[str, str] | None = None) -> RawResponse:
    return RawResponse(200, dict(headers or {}), entry.body)


async def cached_get(
    cache: CacheProvider,
    key: str,
    send: Sender,
    *,
    ttl_seconds: float = 86_400.0,
    fresh_for_seconds: float = 0.0,
    serve_stale_on_error: bool = True,
    now: Callable[[], float] = time.time,
    **retry_options: Any,
) -> CachedResult:
    """GET through the cache. ``retry_options`` go to :func:`send_with_retry`."""
    entry = decode_entry(await cache.get(key))
    current = now()
    if entry is not None and fresh_for_seconds > 0 and current - entry.stored_at < fresh_for_seconds:
        return CachedResult(_from_entry(entry), entry.stored_at, from_cache=True)

    try:
        response = await send_with_retry(send, extra_headers=conditional_headers(entry), now=now, **retry_options)
    except (GitHubUnavailableError, GitHubRateLimitedError):
        if entry is not None and serve_stale_on_error:
            return CachedResult(_from_entry(entry), entry.stored_at, from_cache=True, stale=True)
        raise

    if response.status == 304:
        if entry is None:
            raise GitHubError("GitHub answered 304 to a request that had no validators")
        refreshed = CacheEntry(entry.etag, entry.last_modified, entry.body, current)
        await cache.set(key, encode_entry(refreshed), ttl_seconds)
        return CachedResult(_from_entry(entry, response.headers), current, from_cache=True, revalidated=True)

    etag = response.headers.get("etag")
    last_modified = response.headers.get("last-modified")
    # Only a 200 is stored. A 204 has no body to serve again, so keeping one would turn "nothing to list" into
    # an empty 200 the next time it was read from the cache.
    if response.status == 200 and (etag or last_modified) and len(response.body) <= MAX_CACHED_BODY_BYTES:
        await cache.set(key, encode_entry(CacheEntry(etag, last_modified, response.body, current)), ttl_seconds)
    return CachedResult(response, current)
