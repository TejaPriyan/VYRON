"""Tracks GitHub's primary rate-limit state from response headers (internal status + early stop)."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from vyron_api.github.ratelimit import RateLimitState, parse_rate_limit_headers
from vyron_api.providers.github import GitHubRateLimitedError


class RateLimitBudget:
    """Per-resource (``core``, ``search``, ``graphql`` ...) view of the latest headers seen.

    ``check`` refuses to start a request when the quota is at or below ``reserve`` and the window
    has not reset yet, so background ingestion leaves headroom for interactive requests.
    """

    def __init__(self, *, reserve: int = 10, now: Callable[[], float] = time.time) -> None:
        if reserve < 0:
            raise ValueError("reserve must not be negative")
        self._reserve = reserve
        self._now = now
        self._states: dict[str, RateLimitState] = {}

    def update(self, headers: Mapping[str, str]) -> None:
        state = parse_rate_limit_headers(headers)
        if state.limit is None or state.remaining is None:
            return  # response carried no rate-limit information
        self._states[state.resource or "core"] = state

    def check(self, resource: str = "core") -> None:
        state = self._states.get(resource)
        if state is None or state.remaining is None or state.reset_epoch is None:
            return
        wait = state.reset_epoch - self._now()
        if wait <= 0:
            self._states.pop(resource, None)  # window has rolled over; stale state
            return
        if state.remaining <= self._reserve:
            raise GitHubRateLimitedError(f"{resource} rate-limit budget exhausted ({state.remaining} left)", wait + 1.0)

    def snapshot(self) -> dict[str, RateLimitState]:
        return dict(self._states)
