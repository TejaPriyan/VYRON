"""Async request runner: sends one logical GitHub request with documented retry behavior.

The transport is injected (``send``), as are the clock and ``sleep``, so everything here is testable
without a network. Long waits are not slept through: if GitHub asks for more than
``max_wait_seconds`` the runner raises ``GitHubRateLimitedError`` carrying the wait, so a background
job can be rescheduled instead of holding a worker.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from vyron_api.github.ratelimit import decide_retry
from vyron_api.providers.github import (
    GitHubAuthError,
    GitHubError,
    GitHubForbiddenError,
    GitHubMovedError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
    GitHubValidationError,
)

_MOVED_STATUSES = frozenset({301, 302, 307, 308})
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_MESSAGE_CHARS = 200


@dataclass(frozen=True, slots=True)
class RawResponse:
    status: int
    headers: Mapping[str, str]  # lower-cased names
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubError("GitHub returned a body that is not valid JSON") from exc


Sender = Callable[[Mapping[str, str]], Awaitable[RawResponse]]
Sleeper = Callable[[float], Awaitable[None]]


def error_message(response: RawResponse) -> str:
    """GitHub's ``message`` field, sanitized and truncated; empty if absent or not JSON."""
    try:
        data = json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        return ""
    message = data.get("message") if isinstance(data, dict) else None
    if not isinstance(message, str):
        return ""
    return _CONTROL_RE.sub(" ", message).strip()[:_MAX_MESSAGE_CHARS]


def raise_for_status(response: RawResponse) -> None:
    """Map a non-success response to a typed error. 2xx and 304 pass."""
    status = response.status
    if 200 <= status < 300 or status == 304:
        return
    detail = error_message(response)
    suffix = f": {detail}" if detail else ""
    if status == 401:
        raise GitHubAuthError(f"GitHub rejected the credentials{suffix}")
    if status in (403, 451):
        raise GitHubForbiddenError(f"GitHub denied access{suffix}")
    if status in (404, 410):
        raise GitHubNotFoundError(f"GitHub resource not found{suffix}")
    if status in (400, 422):
        raise GitHubValidationError(f"GitHub rejected the request{suffix}")
    if status in _MOVED_STATUSES:
        raise GitHubMovedError(f"GitHub redirected the request (HTTP {status}); redirects are not followed")
    if 300 <= status < 400:
        raise GitHubError(f"GitHub redirected the request (HTTP {status}); redirects are not followed")
    raise GitHubError(f"unexpected GitHub response (HTTP {status}){suffix}")


async def send_with_retry(
    send: Sender,
    *,
    extra_headers: Mapping[str, str] | None = None,
    sleep: Sleeper = asyncio.sleep,
    now: Callable[[], float] = time.time,
    jitter: Callable[[], float] = lambda: 0.0,
    max_retries: int = 5,
    max_wait_seconds: float = 120.0,
    base_backoff: float = 1.0,
) -> RawResponse:
    """Send once, then retry up to ``max_retries`` more times (so at most ``max_retries + 1`` sends)."""
    max_attempts = max_retries
    headers = dict(extra_headers or {})
    attempt = 0
    while True:
        try:
            response = await send(headers)
        except (OSError, TimeoutError) as exc:
            decision = decide_retry(
                503, {}, attempt=attempt, now=now(), max_attempts=max_attempts,
                base_backoff=base_backoff, jitter=jitter(),
            )  # fmt: skip
            if not decision.retry:
                raise GitHubUnavailableError("GitHub could not be reached") from exc
            await sleep(decision.wait_seconds)
            attempt += 1
            continue

        if 200 <= response.status < 300 or response.status == 304:
            return response

        decision = decide_retry(
            response.status, response.headers, attempt=attempt, now=now(),
            message=error_message(response), max_attempts=max_attempts,
            base_backoff=base_backoff, jitter=jitter(),
        )  # fmt: skip
        limited = response.status in (403, 429) and decision.reason != "forbidden"
        if decision.retry:
            if decision.wait_seconds > max_wait_seconds:
                if limited:
                    raise GitHubRateLimitedError(
                        "GitHub rate limit reached; retry later", decision.wait_seconds
                    )
                raise GitHubUnavailableError("GitHub is failing; retry later")
            await sleep(decision.wait_seconds)
            attempt += 1
            continue
        if decision.reason == "max-attempts":
            if limited:
                raise GitHubRateLimitedError("GitHub rate limit persisted after retries", 0.0)
            raise GitHubUnavailableError("GitHub kept failing after retries")
        raise_for_status(response)
        return response  # unreachable for non-success statuses; keeps the type checker honest
