"""OpenAPI 3.1 document generated from the route registry and the schema components.

Nothing here is hand-written per route, so the published contract is the registry. Planned routes
are included and marked ``x-vyron-status: planned`` (and say so in their description) so clients can
see the intended surface without mistaking it for a working one.
"""

from __future__ import annotations

import json
from typing import Any

from vyron_api.api import limits
from vyron_api.api.errors import ErrorCode
from vyron_api.api.routes import ROUTES, Auth, Param, Route, Status
from vyron_api.api.schemas import COMPONENTS, PLANNED_COMPONENTS, Schema, ref

OPENAPI_VERSION = "3.1.0"
SESSION_COOKIE = "vyron_session"

TAG_DESCRIPTIONS: dict[str, str] = {
    "system": "Liveness, readiness, service metadata and vocabularies.",
    "entities": "Technologies, languages, frameworks, companies, repositories and other graph nodes.",
    "technologies": "Technology-like entities.",
    "companies": "Companies and organizations.",
    "search": "Ranked, typo-tolerant entity search.",
    "graph": "Neighborhoods, shortest paths and whole-graph statistics.",
    "timeline": "Dated events. Predictions are labelled and opt-in.",
    "github": "GitHub link parsing and (planned) repository analysis.",
    "repositories": "Repository intelligence (planned).",
    "registries": "Packages as PyPI and npm describe them, read when asked: declared dependencies with their source.",
    "evidence": "Sources and evidence behind statements (planned).",
    "research": "AI-assisted research over retrieved evidence (planned).",
    "future": "Future Lab scenarios; always labelled 'predicted' (planned).",
    "auth": "Account creation and sessions (planned).",
    "user": "The signed-in user's bookmarks and saved maps (planned).",
}

# error code -> reusable response component name
_ERROR_RESPONSES: dict[ErrorCode, tuple[str, str]] = {
    ErrorCode.VALIDATION_ERROR: ("ValidationError", "A parameter or body field is invalid."),
    ErrorCode.BAD_REQUEST: ("BadRequest", "The request is malformed."),
    ErrorCode.UNAUTHENTICATED: ("Unauthenticated", "A valid session is required."),
    ErrorCode.FORBIDDEN: ("Forbidden", "The session is not allowed to do this."),
    ErrorCode.NOT_FOUND: ("NotFound", "The resource does not exist."),
    ErrorCode.RATE_LIMITED: ("RateLimited", "Too many requests; see the Retry-After header."),
    ErrorCode.INTERNAL_ERROR: ("InternalError", "An unexpected error occurred."),
    ErrorCode.NOT_IMPLEMENTED: ("NotImplemented", "Specified but not implemented yet."),
    ErrorCode.SERVICE_UNAVAILABLE: ("ServiceUnavailable", "A backing service is unavailable."),
}
_STATUS_FOR_RESPONSE = {
    "ValidationError": "422", "BadRequest": "400", "Unauthenticated": "401", "Forbidden": "403",
    "NotFound": "404", "RateLimited": "429", "InternalError": "500", "NotImplemented": "501",
    "ServiceUnavailable": "503",
}  # fmt: skip


def _parameter(param: Param) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": param.name,
        "in": param.location,
        "required": param.required,
        "schema": param.schema,
    }
    if param.description:
        item["description"] = param.description
    if param.schema.get("type") == "array":
        item["style"] = "form"
        item["explode"] = True
    return item


_HEADER_COMPONENTS: dict[str, dict[str, Any]] = {
    "XRequestID": {
        "description": "Echoes a valid inbound X-Request-ID, or a generated one.",
        "schema": {"type": "string"},
    },
    "RateLimitLimit": {"description": "Requests allowed per window.", "schema": {"type": "integer"}},
    "RateLimitRemaining": {"description": "Requests left in this window.", "schema": {"type": "integer"}},
    "RateLimitReset": {"description": "Seconds until the window resets.", "schema": {"type": "integer"}},
    "ETag": {"description": "Send as If-None-Match to receive 304.", "schema": {"type": "string"}},
    "CacheControl": {"schema": {"type": "string"}},
}


def _success_headers(route: Route) -> dict[str, Any]:
    names = {
        "X-Request-ID": "XRequestID",
        "RateLimit-Limit": "RateLimitLimit",
        "RateLimit-Remaining": "RateLimitRemaining",
        "RateLimit-Reset": "RateLimitReset",
    }
    if route.cache_seconds is not None:
        names.update({"ETag": "ETag", "Cache-Control": "CacheControl"})
    return {header: {"$ref": f"#/components/headers/{component}"} for header, component in names.items()}


def _error_codes_for(route: Route) -> list[ErrorCode]:
    codes = [ErrorCode.RATE_LIMITED, ErrorCode.INTERNAL_ERROR]
    if route.params or route.request_schema:
        codes.insert(0, ErrorCode.VALIDATION_ERROR)
    if route.path_params and route.method == "GET":
        codes.insert(1, ErrorCode.NOT_FOUND)
    if route.auth is Auth.SESSION:
        codes.insert(0, ErrorCode.UNAUTHENTICATED)
    if route.operation_id == "health_ready":
        codes.insert(0, ErrorCode.SERVICE_UNAVAILABLE)
    if route.status is Status.PLANNED:
        codes.append(ErrorCode.NOT_IMPLEMENTED)
    return codes


def _operation(route: Route) -> dict[str, Any]:
    planned = route.status is Status.PLANNED
    description = route.description
    if planned:
        description = ("PLANNED: specified but not implemented; currently answers 501. " + description).strip()
    responses: dict[str, Any] = {}
    success: dict[str, Any] = {"description": "Success", "headers": _success_headers(route)}
    if route.response_schema:
        success["content"] = {"application/json": {"schema": ref(route.response_schema)}}
    responses[str(route.success_status)] = success
    if route.cache_seconds is not None:
        responses["304"] = {"description": "Not modified (If-None-Match matched)."}
    for code in _error_codes_for(route):
        name, _ = _ERROR_RESPONSES[code]
        responses[_STATUS_FOR_RESPONSE[name]] = {"$ref": f"#/components/responses/{name}"}
    operation: dict[str, Any] = {
        "operationId": route.operation_id,
        "summary": route.summary,
        "tags": list(route.tags),
        "x-vyron-status": route.status.value,
        "x-vyron-rate-bucket": route.rate_bucket.value,
        "security": [{"sessionCookie": []}] if route.auth is Auth.SESSION else [],
        "responses": responses,
    }
    if description:
        operation["description"] = description
    if route.params:
        operation["parameters"] = [_parameter(p) for p in route.params]
    if route.request_schema:
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": ref(route.request_schema)}},
        }
    return operation


def _schemas() -> dict[str, Schema]:
    published: dict[str, Schema] = {}
    for name, schema in COMPONENTS.items():
        published[name] = {**schema, "x-vyron-status": "planned"} if name in PLANNED_COMPONENTS else dict(schema)
    return published


def build_openapi(version: str = "0.1.0") -> dict[str, Any]:
    paths: dict[str, dict[str, Any]] = {}
    for route in ROUTES:
        paths.setdefault(route.path, {})[route.method.lower()] = _operation(route)

    used_tags = sorted({tag for route in ROUTES for tag in route.tags})
    return {
        "openapi": OPENAPI_VERSION,
        "jsonSchemaDialect": "https://json-schema.org/draft/2020-12/schema",
        "info": {
            "title": "VYRON API",
            "version": version,
            "description": (
                "Read API for the VYRON technology knowledge graph. Seed data is development data and "
                "is labelled in responses. Operations marked x-vyron-status: planned are specified but "
                "not implemented. This document is generated from the route registry; do not edit it."
            ),
        },
        "servers": [{"url": "/", "description": "Same origin (the web app proxies server-side)."}],
        "tags": [{"name": tag, "description": TAG_DESCRIPTIONS[tag]} for tag in used_tags],
        "paths": paths,
        "components": {
            "schemas": _schemas(),
            "responses": {
                name: {
                    "description": text,
                    "content": {"application/json": {"schema": ref("Error")}},
                }
                for name, text in _ERROR_RESPONSES.values()
            },
            "headers": _HEADER_COMPONENTS,
            "securitySchemes": {
                "sessionCookie": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": SESSION_COOKIE,
                    "description": "HttpOnly, Secure, SameSite=Lax session cookie (planned).",
                }
            },
        },
        "x-vyron-limits": {
            "max_page_size": limits.MAX_PAGE_SIZE,
            "max_graph_depth": limits.MAX_GRAPH_DEPTH,
            "max_graph_nodes": limits.MAX_GRAPH_NODES,
        },
    }


def render_openapi_json(version: str = "0.1.0") -> str:
    return json.dumps(build_openapi(version), indent=2, ensure_ascii=False) + "\n"
