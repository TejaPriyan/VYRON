"""Pure rate-limit logic for the GitHub REST API (no I/O, so it is fully unit-testable).

Behavior follows GitHub's documented guidance (checked 2026-10-05):
- primary limit state comes from the ``x-ratelimit-*`` response headers;
- on a primary or secondary limit (403/429) honor ``retry-after`` if present, otherwise wait until
  ``x-ratelimit-reset`` when the quota is exhausted, otherwise wait at least one minute and back
  off exponentially on repeated failures;
- a plain 403 (permissions, abuse blocks) is not a rate limit and is never retried blindly.

``retry-after`` is parsed as integer seconds only; an HTTP-date form is ignored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

MIN_SECONDARY_WAIT_SECONDS = 60.0
_RATE_LIMIT_STATUSES = frozenset({403, 429})
_TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RateLimitState:
    limit: int | None = None
    remaining: int | None = None
    used: int | None = None
    reset_epoch: int | None = None
    resource: str | None = None
    retry_after_seconds: int | None = None

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    wait_seconds: float
    reason: str


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        number = int(value.strip())
    except ValueError:
        return None
    return number if number >= 0 else None


def parse_rate_limit_headers(headers: Mapping[str, str]) -> RateLimitState:
    lowered = {key.lower(): value for key, value in headers.items()}
    resource = lowered.get("x-ratelimit-resource")
    return RateLimitState(
        limit=_to_int(lowered.get("x-ratelimit-limit")),
        remaining=_to_int(lowered.get("x-ratelimit-remaining")),
        used=_to_int(lowered.get("x-ratelimit-used")),
        reset_epoch=_to_int(lowered.get("x-ratelimit-reset")),
        resource=resource.strip() if resource else None,
        retry_after_seconds=_to_int(lowered.get("retry-after")),
    )


def decide_retry(
    status: int,
    headers: Mapping[str, str],
    *,
    attempt: int,
    now: float,
    message: str = "",
    max_attempts: int = 5,
    base_backoff: float = 1.0,
    jitter: float = 0.0,
) -> RetryDecision:
    """Decide whether and how long to wait before retrying a failed GitHub request.

    ``attempt`` is the number of retries already made (0 on the first failure); ``now`` is epoch
    seconds. The caller owns sleeping or rescheduling, so long waits can go back to the job queue.
    """
    if attempt < 0:
        raise ValueError("attempt must not be negative")
    if jitter < 0:
        raise ValueError("jitter must not be negative")
    if attempt >= max_attempts:
        return RetryDecision(False, 0.0, "max-attempts")

    if status in _TRANSIENT_STATUSES:
        return RetryDecision(True, base_backoff * 2**attempt + jitter, "transient-server-error")

    if status in _RATE_LIMIT_STATUSES:
        state = parse_rate_limit_headers(headers)
        limited = (
            status == 429
            or state.retry_after_seconds is not None
            or state.exhausted
            or "rate limit" in message.lower()
        )
        if not limited:
            return RetryDecision(False, 0.0, "forbidden")
        if state.retry_after_seconds is not None:
            return RetryDecision(True, float(state.retry_after_seconds) + jitter, "retry-after")
        if state.exhausted and state.reset_epoch is not None:
            wait = max(0.0, state.reset_epoch - now) + 1.0
            return RetryDecision(True, wait + jitter, "primary-limit-reset")
        wait = MIN_SECONDARY_WAIT_SECONDS * 2**attempt
        return RetryDecision(True, wait + jitter, "secondary-limit-backoff")

    return RetryDecision(False, 0.0, "not-retryable")
