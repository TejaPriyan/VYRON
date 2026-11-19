"""Serve the VYRON map prototype and its API from one origin, with the standard library only.

    python3 apps/web/prototype/serve.py            # http://127.0.0.1:8080/
    python3 apps/web/prototype/serve.py --port 0   # pick a free port and print it

DEVELOPMENT ONLY. The API is the real application (``vyron_api``) over the *development seed*, behind
the real rate limiter, so what the page shows is exactly what the HTTP contract returns. The one thing that
is not seed data is the repository page, which reads the real GitHub REST API when you ask (``--no-github``
turns that off; ``GITHUB_TOKEN`` raises GitHub's limit), and the dependencies page, which reads one real PyPI or npm
package at a time (``--no-registries`` turns that off). The static
side is a fixed table of files plus the compiled scripts under ``dist/js``; nothing else on disk can be
reached. Compile the scripts first with ``python3 scripts/build_prototype.py`` (needs only ``tsc``).

The page's Content-Security-Policy allows no inline script and no inline style, which is why the code
never sets either.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parents[3]
PROTOTYPE = ROOT / "apps" / "web" / "prototype"
DIST_JS = PROTOTYPE / "dist" / "js"
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from vyron_api.api.app import Application  # noqa: E402
from vyron_api.api.bootstrap import build_seed_application  # noqa: E402
from vyron_api.api.hostcheck import is_loopback_bind  # noqa: E402
from vyron_api.api.wsgi import make_wsgi_app  # noqa: E402
from vyron_api.devserver import StartupError, configure  # noqa: E402

BANNER = (
    "VYRON map prototype: seed graph, live GitHub on the repository page, live PyPI and npm on the "
    "dependencies page, not for production use."
)

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)

#: The only files served outside ``/js``. Exact match, so there is no path to traverse.
STATIC_FILES: dict[str, tuple[Path, str]] = {
    "/": (PROTOTYPE / "index.html", "text/html; charset=utf-8"),
    "/index.html": (PROTOTYPE / "index.html", "text/html; charset=utf-8"),
    "/repository.html": (PROTOTYPE / "repository.html", "text/html; charset=utf-8"),
    "/dependencies.html": (PROTOTYPE / "dependencies.html", "text/html; charset=utf-8"),
    "/compare.html": (PROTOTYPE / "compare.html", "text/html; charset=utf-8"),
    "/prototype.css": (PROTOTYPE / "prototype.css", "text/css; charset=utf-8"),
    "/tokens.css": (ROOT / "apps" / "web" / "src" / "styles" / "tokens.css", "text/css; charset=utf-8"),
    "/favicon.svg": (PROTOTYPE / "favicon.svg", "image/svg+xml"),
    "/robots.txt": (PROTOTYPE / "robots.txt", "text/plain; charset=utf-8"),
    "/sitemap.xml": (PROTOTYPE / "sitemap.xml", "application/xml; charset=utf-8"),
    "/llms.txt": (PROTOTYPE / "llms.txt", "text/plain; charset=utf-8"),
    "/schema.jsonld": (PROTOTYPE / "schema.jsonld", "application/ld+json; charset=utf-8"),
    "/site.webmanifest": (PROTOTYPE / "site.webmanifest", "application/manifest+json; charset=utf-8"),
}

SECURITY_HEADERS = (
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cache-Control", "no-store"),
)

StartResponse = Callable[..., Any]
WsgiApp = Callable[[dict[str, Any], StartResponse], Iterable[bytes]]


def _resolve_script(path: str, dist: Path) -> Path | None:
    """A compiled ``.js`` file under ``dist``, or None. Rejects anything that escapes ``dist``."""
    relative = path[len("/js/") :]
    if not relative.endswith(".js") or "\\" in relative or "\x00" in relative:
        return None
    candidate = (dist / relative).resolve()
    try:
        candidate.relative_to(dist.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def make_app(
    *,
    dist: Path = DIST_JS,
    rate_limit: bool = True,
    application: Application | None = None,
    trusted_proxy_hops: int = 0,
) -> WsgiApp:
    """The composite application: ``/api`` and ``/health`` go to the API, the rest is static.

    ``application`` is the API to serve; without one it is the seed app with GitHub switched off.
    """
    api = make_wsgi_app(
        application or build_seed_application(rate_limit=rate_limit),
        trusted_proxy_hops=trusted_proxy_hops,
    )

    def respond(start_response: StartResponse, status: str, body: bytes, content_type: str, head: bool) -> list[bytes]:
        headers = [("Content-Type", content_type), ("Content-Length", str(len(body))), *SECURITY_HEADERS]
        start_response(status, headers)
        return [] if head else [body]

    def app(environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        path = str(environ.get("PATH_INFO", "/"))
        if path.startswith(("/api/", "/health/")):
            return api(environ, start_response)
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        if method not in ("GET", "HEAD"):
            start_response(
                "405 Method Not Allowed",
                [("Allow", "GET, HEAD"), ("Content-Length", "0"), *SECURITY_HEADERS],
            )
            return []
        head = method == "HEAD"
        target: tuple[Path, str] | None = None
        if path in STATIC_FILES:
            target = STATIC_FILES[path]
        elif path.startswith("/js/"):
            script = _resolve_script(path, dist)
            if script is not None:
                target = (script, "text/javascript; charset=utf-8")
        if target is None:
            note = b"Not found.\n"
            return respond(start_response, "404 Not Found", note, "text/plain; charset=utf-8", head)
        file, content_type = target
        if not file.is_file():
            hint = b"Missing build output. Run: python3 scripts/build_prototype.py\n"
            return respond(start_response, "404 Not Found", hint, "text/plain; charset=utf-8", head)
        return respond(start_response, "200 OK", file.read_bytes(), content_type, head)

    return app


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=BANNER)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--no-rate-limit",
        action="store_true",
        help="turn the API rate limiter off (for automated browser runs that reload the page quickly)",
    )
    parser.add_argument(
        "--no-github", action="store_true", help="do not call GitHub; the repository page will say it is off"
    )
    parser.add_argument(
        "--no-registries",
        action="store_true",
        help="do not call PyPI or npm; the dependencies page will say it is off",
    )
    parser.add_argument(
        "--allow-token-on-network",
        action="store_true",
        help="use GITHUB_TOKEN even though the address is not loopback (otherwise refused)",
    )
    args = parser.parse_args(argv)
    try:
        application, github_line = configure(
            args.host,
            github=not args.no_github,
            registries=not args.no_registries,
            allow_token_on_network=args.allow_token_on_network,
            rate_limit=not args.no_rate_limit,
        )
    except StartupError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if not (DIST_JS / "apps" / "web" / "prototype" / "src" / "main.js").is_file():
        print("Build output is missing. Run first:  python3 scripts/build_prototype.py", file=sys.stderr)
        return 2
    server = make_server(args.host, args.port, make_app(application=application), handler_class=_QuietHandler)
    host_line = "any Host header" if not is_loopback_bind(args.host) else "loopback Host headers only"
    print(f"{BANNER}\n{github_line}\nAnswers to: {host_line}", flush=True)
    print(f"Listening on http://{args.host}:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
