"""One error shape for the whole API, and the mapping from internal exceptions to it.

Every non-2xx response is ``{"error": {"code", "message", "request_id", "details"?}}``. Messages are
written for API consumers; internals (stack traces, SQL, upstream response bodies, tokens) never
appear in them. Unknown exceptions become a generic ``internal_error``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from vyron_api.domain.rules import InvalidRelationshipError
from vyron_api.github.normalize import MalformedGitHubPayloadError
from vyron_api.github.url import InvalidRepoReferenceError
from vyron_api.providers.ai import AIUnavailableError
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
from vyron_api.providers.graph import EntityNotFoundError, GraphUnavailableError
from vyron_api.registries.errors import (
    InvalidPackageNameError,
    PackageNotFoundError,
    RegistryError,
    RegistryRateLimitedError,
)


class ErrorCode(StrEnum):
    BAD_REQUEST = "bad_request"
    VALIDATION_ERROR = "validation_error"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONFLICT = "conflict"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"
    NOT_IMPLEMENTED = "not_implemented"
    UPSTREAM_ERROR = "upstream_error"
    UPSTREAM_RATE_LIMITED = "upstream_rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"


STATUS_FOR_CODE: Mapping[ErrorCode, int] = {
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.CONFLICT: 409,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.NOT_IMPLEMENTED: 501,
    ErrorCode.UPSTREAM_ERROR: 502,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.UPSTREAM_RATE_LIMITED: 503,
}

MAX_DETAILS = 20
MAX_RETRY_AFTER_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class FieldIssue:
    field: str
    issue: str


class ApiError(Exception):
    """An error with a stable code, a safe message and optional per-field issues."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: Sequence[FieldIssue] = (),
        *,
        retry_after_seconds: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.details = tuple(details)
        self.retry_after_seconds = retry_after_seconds
        self.headers = dict(headers or {})

    @property
    def status(self) -> int:
        return STATUS_FOR_CODE[self.code]


def validation_error(issues: Sequence[FieldIssue], message: str = "request validation failed") -> ApiError:
    return ApiError(ErrorCode.VALIDATION_ERROR, message, tuple(issues))


def not_found(what: str) -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, f"{what} not found")


def not_implemented(operation_id: str) -> ApiError:
    return ApiError(
        ErrorCode.NOT_IMPLEMENTED,
        f"{operation_id} is part of the planned API surface but is not implemented yet",
    )


def to_api_error(exc: BaseException) -> ApiError:
    """Map any exception to a safe ``ApiError``. Unknown exceptions never leak their message."""
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, EntityNotFoundError):
        return not_found("entity")
    if isinstance(exc, GraphUnavailableError):
        return ApiError(ErrorCode.SERVICE_UNAVAILABLE, "the graph service is temporarily unavailable")
    if isinstance(exc, InvalidRepoReferenceError):
        return validation_error([FieldIssue("input", str(exc))], "not a valid GitHub reference")
    if isinstance(exc, InvalidRelationshipError):
        return ApiError(ErrorCode.CONFLICT, "that relationship is not allowed between these entity kinds")
    if isinstance(exc, InvalidPackageNameError):
        return validation_error([FieldIssue("name", str(exc))], "not a valid package name")
    if isinstance(exc, PackageNotFoundError):
        return not_found("package")
    if isinstance(exc, RegistryRateLimitedError):
        return ApiError(
            ErrorCode.UPSTREAM_RATE_LIMITED, "the package registry is limiting requests; retry later",
            retry_after_seconds=60,
        )  # fmt: skip
    if isinstance(exc, RegistryError):
        # The registry's own words never reach this message: the text is fixed in ``registries/errors.py`` and
        # ``registries/http.py`` and names only the registry.
        return ApiError(ErrorCode.UPSTREAM_ERROR, str(exc))
    if isinstance(exc, GitHubNotFoundError):
        return not_found("GitHub repository")
    if isinstance(exc, GitHubMovedError):
        return ApiError(ErrorCode.NOT_FOUND, "GitHub repository was renamed or moved; use its current name")
    if isinstance(exc, GitHubValidationError):
        return validation_error([], "GitHub rejected the request")
    if isinstance(exc, GitHubRateLimitedError):
        wait = max(1, min(MAX_RETRY_AFTER_SECONDS, int(exc.wait_seconds) + 1))
        return ApiError(
            ErrorCode.UPSTREAM_RATE_LIMITED,
            "GitHub rate limit reached; retry later",
            retry_after_seconds=wait,
        )
    # An expired server token, a blocked request or an unreachable GitHub is not the caller's fault. The messages
    # say which of these it is, because the fix differs, and never carry GitHub's own text or any credential.
    if isinstance(exc, GitHubAuthError):
        return ApiError(ErrorCode.UPSTREAM_ERROR, "the server's GitHub token was rejected by GitHub")
    if isinstance(exc, GitHubForbiddenError):
        return ApiError(ErrorCode.UPSTREAM_ERROR, "GitHub denied the request")
    if isinstance(exc, GitHubUnavailableError):
        return ApiError(ErrorCode.UPSTREAM_ERROR, "GitHub could not be reached")
    if isinstance(exc, (GitHubError, MalformedGitHubPayloadError)):
        return ApiError(ErrorCode.UPSTREAM_ERROR, "GitHub returned a response VYRON could not use")
    if isinstance(exc, AIUnavailableError):
        return ApiError(ErrorCode.SERVICE_UNAVAILABLE, "the AI research layer is unavailable")
    return ApiError(ErrorCode.INTERNAL_ERROR, "an unexpected error occurred")


def error_body(error: ApiError, request_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": error.code.value,
        "message": error.message,
        "request_id": request_id,
    }
    if error.details:
        body["details"] = [{"field": issue.field, "issue": issue.issue} for issue in error.details[:MAX_DETAILS]]
    return {"error": body}


def error_headers(error: ApiError) -> dict[str, str]:
    headers = dict(error.headers)
    if error.retry_after_seconds is not None:
        headers["Retry-After"] = str(error.retry_after_seconds)
    return headers
