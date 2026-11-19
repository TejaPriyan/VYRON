"""Per-client request rate limiting.

``FixedWindowRateLimiter`` is a single-process, in-memory implementation: right for tests, local
development and one-instance deployments. With several API instances the counters must be shared, so
production needs a Redis-backed implementation of the same ``RateLimiter`` protocol. That adapter is
NOT written yet (UNVERIFIED: no Redis client can be installed here).

Fixed windows allow up to twice the limit across a window boundary; that is accepted for coarse abuse
protection. Credential endpoints get the strictest bucket, and a per-account limit must be layered
on top when authentication exists, because a per-IP limit alone does not stop a distributed attack on
one account.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from vyron_api.api.routes import RateBucket


@dataclass(frozen=True, slots=True)
class Limit:
    max_requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.max_requests < 1 or self.window_seconds < 1:
            raise ValueError("limits must be positive")


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int  # seconds until the current window ends


DEFAULT_LIMITS: Mapping[RateBucket, Limit] = {
    RateBucket.HEALTH: Limit(600, 60),
    RateBucket.READ: Limit(120, 60),
    RateBucket.SEARCH: Limit(60, 60),
    RateBucket.GRAPH: Limit(60, 60),
    RateBucket.GITHUB: Limit(30, 60),
    RateBucket.REGISTRY: Limit(30, 60),
    RateBucket.AUTH: Limit(10, 60),
    RateBucket.AI: Limit(10, 60),
    RateBucket.WRITE: Limit(30, 60),
}


class RateLimiter(Protocol):
    def check(self, client_key: str, bucket: RateBucket) -> RateDecision:
        """Count one request for ``client_key`` in ``bucket`` and say whether it is allowed."""


class FixedWindowRateLimiter:
    def __init__(
        self,
        limits: Mapping[RateBucket, Limit] = DEFAULT_LIMITS,
        *,
        max_keys: int = 10_000,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        missing = set(RateBucket) - set(limits)
        if missing:
            raise ValueError(f"no limit configured for: {sorted(b.value for b in missing)}")
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        self._limits = dict(limits)
        self._max_keys = max_keys
        self._now = now
        self._lock = threading.Lock()
        # (client, bucket) -> (window_start, count); ordered by last use for bounded memory
        self._windows: OrderedDict[tuple[str, RateBucket], tuple[float, int]] = OrderedDict()

    def check(self, client_key: str, bucket: RateBucket) -> RateDecision:
        limit = self._limits[bucket]
        key = (client_key, bucket)
        with self._lock:
            now = self._now()
            start, count = self._windows.get(key, (now, 0))
            if now - start >= limit.window_seconds:
                start, count = now, 0
            reset = max(1, int(limit.window_seconds - (now - start) + 0.999))
            allowed = count < limit.max_requests
            if allowed:
                count += 1
            self._windows[key] = (start, count)
            self._windows.move_to_end(key)
            while len(self._windows) > self._max_keys:
                self._windows.popitem(last=False)  # forget the least recently seen client
            return RateDecision(allowed, limit.max_requests, max(0, limit.max_requests - count), reset)
