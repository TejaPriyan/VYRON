"""ASGI/FastAPI binding for ``Application``.   ** UNVERIFIED **

FastAPI is not installable in the environment this code was written in, so this module has never been
imported or run. It is deliberately thin so there is little here to get wrong: every behaviour that
matters (routing, validation, errors, headers, rate limits) lives in ``vyron_api.api.app`` and is
tested with the standard library. FastAPI is used as the ASGI host (server integration, lifespan,
middleware ecosystem), not to re-describe the contract; the contract is ``docs/openapi.json``,
generated from the route registry.

Run (once dependencies can be installed):
    uvicorn --factory vyron_api.fastapi_app:create_app --host 0.0.0.0 --port 8000

First things to verify when it can run: that a request reaches ``Application.handle`` with the same
path, query string and headers the WSGI tests exercise; that oversized bodies are cut off after
``MAX_BODY_BYTES + 1`` bytes; and that responses (including 304 with an empty body) are passed
through unchanged. ``tests/test_api_asgi_contract.py`` (to be written when FastAPI is available)
should replay ``tests/test_api_app.SAMPLES`` through ``fastapi.testclient.TestClient``.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi import Response as FastAPIResponse

from vyron_api.api import limits
from vyron_api.api.app import Application
from vyron_api.api.app import Request as CoreRequest
from vyron_api.api.bootstrap import build_application
from vyron_api.api.clientip import select_client_ip
from vyron_api.config import Settings, load_settings

logger = logging.getLogger("vyron.api")

_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


async def _read_limited_body(request: Request) -> bytes:
    """Read at most ``MAX_BODY_BYTES + 1`` bytes; the core turns one byte over into a 413."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limits.MAX_BODY_BYTES:
        return b"x" * (limits.MAX_BODY_BYTES + 1)  # reject without buffering the body
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        chunks.append(chunk)
        size += len(chunk)
        if size > limits.MAX_BODY_BYTES:
            return b"x" * (limits.MAX_BODY_BYTES + 1)
    return b"".join(chunks)


def create_app(settings: Settings | None = None, application: Application | None = None) -> FastAPI:
    settings = settings or load_settings(os.environ)
    logging.basicConfig(level=settings.log_level.upper())
    logger.info("starting", extra={"config": settings.safe_summary()})  # no secret values
    core = application or build_application(settings)

    # The API is JSON only and called server-to-server by the web app: no docs UI, no CORS, no
    # automatically generated schema that could disagree with docs/openapi.json.
    app = FastAPI(title="VYRON API", openapi_url=None, docs_url=None, redoc_url=None)

    @app.api_route("/{full_path:path}", methods=_METHODS, include_in_schema=False)
    async def dispatch(request: Request, full_path: str) -> FastAPIResponse:
        headers = {name.lower(): value for name, value in request.headers.items()}
        core_request = CoreRequest(
            method=request.method.upper(),
            path=request.url.path,
            query_string=request.url.query,
            headers=headers,
            body=await _read_limited_body(request),
            client_ip=select_client_ip(
                request.client.host if request.client else None, headers, settings.trusted_proxy_hops
            ),
        )
        response = await core.handle(core_request)
        return FastAPIResponse(
            content=response.body, status_code=response.status, headers=dict(response.headers)
        )

    return app
