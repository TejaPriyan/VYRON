"""Framework-independent HTTP application: ``Request`` in, ``Response`` out.

All HTTP behaviour lives here (routing, 404/405/501, rate limits, parameter validation, error
envelope, caching headers, ETags, security headers, request ids) so it can be tested with the
standard library and bound to any server. ``wsgi.py`` is a verified stdlib binding; ``fastapi_app.py``
is the intended production binding (UNVERIFIED: FastAPI is not installable here).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from vyron_api.api import limits
from vyron_api.api.errors import (
    ApiError,
    ErrorCode,
    error_body,
    error_headers,
    not_found,
    not_implemented,
    to_api_error,
)
from vyron_api.api.handlers import HANDLERS
from vyron_api.api.hostcheck import host_allowed
from vyron_api.api.params import parse_path_params, parse_query_params
from vyron_api.api.ratelimit import RateDecision, RateLimiter
from vyron_api.api.routes import Auth, Route, Status, find_route, methods_for_path
from vyron_api.services.catalog import CatalogService

logger = logging.getLogger("vyron.api")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_PATH_RE = re.compile(r"^/[\x21-\x7e]*$")

SECURITY_HEADERS: Mapping[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-site",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "X-Frame-Options": "DENY",
}


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    query_string: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)  # lower-case header names
    body: bytes = b""
    client_ip: str = "unknown"


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


def _etag(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    candidates = [part.strip() for part in header.split(",")]
    return "*" in candidates or any(c.removeprefix("W/") == etag for c in candidates)


class Application:
    def __init__(
        self,
        service: CatalogService,
        *,
        rate_limiter: RateLimiter | None = None,
        new_request_id: Callable[[], str] = lambda: uuid.uuid4().hex,
        clock: Callable[[], float] = time.perf_counter,
        allowed_hosts: Collection[str] | None = None,
    ) -> None:
        self._service = service
        self._limiter = rate_limiter
        self._new_request_id = new_request_id
        self._clock = clock
        # None answers to any Host (the right default behind a proxy that routes by host). A collection answers
        # only to those names, which is how the development server defeats DNS rebinding; see hostcheck.py.
        self._allowed_hosts = None if allowed_hosts is None else frozenset(name.lower() for name in allowed_hosts)

    async def handle(self, request: Request) -> Response:
        started = self._clock()
        request_id = self._request_id(request)
        route: Route | None = None
        decision: RateDecision | None = None
        try:
            self._check_host(request)
            route, path_params = self._route(request)
            decision = self._check_rate(request, route)
            response = await self._dispatch(request, route, path_params, request_id)
        except Exception as exc:  # the boundary maps every failure to the error envelope
            error = to_api_error(exc)
            if error.code is ErrorCode.INTERNAL_ERROR:
                logger.error(
                    "unhandled error",
                    extra={"request_id": request_id, "operation_id": route.operation_id if route else None},
                    exc_info=exc,
                )
            elif error.code is ErrorCode.UPSTREAM_ERROR:
                logger.warning(
                    "upstream error: %s",
                    exc,
                    extra={"request_id": request_id, "operation_id": route.operation_id if route else None},
                )
            response = self._error_response(error, request_id)
        headers = dict(response.headers)
        if decision is not None:
            headers.update(self._rate_headers(decision))
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "operation_id": route.operation_id if route else None,
                "status": response.status,
                "duration_ms": round((self._clock() - started) * 1000, 2),
            },
        )
        return Response(response.status, headers, response.body)

    # ---- steps --------------------------------------------------------------------------------
    def _request_id(self, request: Request) -> str:
        supplied = request.headers.get("x-request-id", "")
        return supplied if _REQUEST_ID_RE.fullmatch(supplied) else self._new_request_id()

    def _check_host(self, request: Request) -> None:
        if self._allowed_hosts is not None and not host_allowed(request.headers.get("host"), self._allowed_hosts):
            raise ApiError(ErrorCode.FORBIDDEN, "this server does not answer to that Host header")

    def _route(self, request: Request) -> tuple[Route, dict[str, str]]:
        if len(request.body) > limits.MAX_BODY_BYTES:
            raise ApiError(ErrorCode.PAYLOAD_TOO_LARGE, "request body is too large")
        if not _PATH_RE.fullmatch(request.path):
            raise not_found("route")
        found = find_route(request.method, request.path)
        if found is not None:
            return found
        allowed = methods_for_path(request.path)
        if allowed:
            raise ApiError(
                ErrorCode.METHOD_NOT_ALLOWED,
                f"{request.method} is not allowed on this path",
                headers={"Allow": ", ".join(sorted(allowed))},
            )
        raise not_found("route")

    def _check_rate(self, request: Request, route: Route) -> RateDecision | None:
        if self._limiter is None:
            return None
        decision = self._limiter.check(request.client_ip, route.rate_bucket)
        if not decision.allowed:
            raise ApiError(
                ErrorCode.RATE_LIMITED,
                "too many requests; slow down",
                retry_after_seconds=decision.reset_seconds,
                headers=self._rate_headers(decision),
            )
        return decision

    @staticmethod
    def _rate_headers(decision: RateDecision) -> dict[str, str]:
        return {
            "RateLimit-Limit": str(decision.limit),
            "RateLimit-Remaining": str(decision.remaining),
            "RateLimit-Reset": str(decision.reset_seconds),
        }

    async def _dispatch(
        self, request: Request, route: Route, raw_path_params: Mapping[str, str], request_id: str
    ) -> Response:
        if route.status is Status.PLANNED:
            raise not_implemented(route.operation_id)
        if route.auth is not Auth.NONE:
            # Fail closed: there is no session backend yet, so an authenticated route that was
            # marked implemented by mistake must refuse everyone rather than serve anyone.
            raise ApiError(ErrorCode.UNAUTHENTICATED, "authentication is required")
        handler = HANDLERS.get(route.operation_id)
        if handler is None:  # a registry/handler mismatch is a programming error, never the client's
            raise RuntimeError(f"no handler registered for {route.operation_id}")
        path_params = parse_path_params(route, raw_path_params)
        query_params = parse_query_params(route, request.query_string)
        status, body = await handler(self._service, path_params, query_params)
        return self._json_response(status, body, route, request, request_id)

    # ---- responses ----------------------------------------------------------------------------
    def _json_response(
        self, status: int, body: dict[str, Any], route: Route, request: Request, request_id: str
    ) -> Response:
        payload = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
        headers = {
            **SECURITY_HEADERS,
            "Content-Type": "application/json; charset=utf-8",
            "X-Request-ID": request_id,
        }
        cacheable = status == 200 and route.cache_seconds is not None
        if cacheable:
            etag = _etag(payload)
            headers["ETag"] = etag
            headers["Cache-Control"] = f"public, max-age={route.cache_seconds}"
            if _etag_matches(request.headers.get("if-none-match"), etag):
                return Response(304, headers, b"")
        else:
            headers["Cache-Control"] = "no-store"
        return Response(status, headers, payload)

    def _error_response(self, error: ApiError, request_id: str) -> Response:
        payload = json.dumps(error_body(error, request_id), separators=(",", ":")).encode("utf-8")
        headers = {
            **SECURITY_HEADERS,
            "Content-Type": "application/json; charset=utf-8",
            "X-Request-ID": request_id,
            "Cache-Control": "no-store",
            **error_headers(error),
        }
        return Response(error.status, headers, payload)
