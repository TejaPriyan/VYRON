"""Standard-library WSGI binding for ``Application``.

This is how the HTTP contract is verified end to end over real sockets without any third-party
package. It is a development and test server (``wsgiref`` is not meant for production traffic). The
production binding is ``fastapi_app.py`` behind an ASGI server (UNVERIFIED: not installable here).

Notes on fidelity: WSGI hands us an already percent-decoded path, so the application sees the decoded
path (no route accepts encoded values, so this only produces 404s for odd input). ``X-Forwarded-For``
is ignored unless ``trusted_proxy_hops`` says how many reverse proxies are in front, because a client
can otherwise spoof its address and evade rate limits.
"""

from __future__ import annotations

import asyncio
import http
from collections.abc import Callable, Iterable
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from vyron_api.api import limits
from vyron_api.api.app import Application, Request
from vyron_api.api.clientip import select_client_ip

StartResponse = Callable[..., Any]


def _headers(environ: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            found[key[5:].replace("_", "-").lower()] = str(value)
    return found


def _body(environ: dict[str, Any]) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return b""
    # Never read more than one byte past the limit; the application turns that into a 413.
    return bytes(environ["wsgi.input"].read(min(length, limits.MAX_BODY_BYTES + 1)))


def make_wsgi_app(application: Application, *, trusted_proxy_hops: int = 0) -> Callable[..., Iterable[bytes]]:
    def app(environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        headers = _headers(environ)
        request = Request(
            method=str(environ.get("REQUEST_METHOD", "GET")).upper(),
            path=str(environ.get("PATH_INFO", "/")),
            query_string=str(environ.get("QUERY_STRING", "")),
            headers=headers,
            body=_body(environ),
            client_ip=select_client_ip(environ.get("REMOTE_ADDR"), headers, trusted_proxy_hops),
        )
        response = asyncio.run(application.handle(request))
        phrase = http.HTTPStatus(response.status).phrase
        out_headers = [(name, value) for name, value in response.headers.items()]
        out_headers.append(("Content-Length", str(len(response.body))))
        start_response(f"{response.status} {phrase}", out_headers)
        return [response.body]

    return app


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return  # request logging is done by the application (without query strings)


def make_dev_server(
    application: Application, host: str = "127.0.0.1", port: int = 8000, *, trusted_proxy_hops: int = 0
) -> WSGIServer:
    return make_server(
        host, port, make_wsgi_app(application, trusted_proxy_hops=trusted_proxy_hops),
        handler_class=_QuietHandler,
    )  # fmt: skip
