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
never sets either. HTML routes also honor ``Accept: text/markdown`` with ``Vary: Accept`` for AI agents.
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

DISCOVERY_LINK_HEADER = (
    '</llms.txt>; rel="alternate"; type="text/plain", '
    '</llms-full.txt>; rel="alternate"; type="text/plain", '
    '</schema.jsonld>; rel="alternate"; type="application/ld+json"'
)

#: The only files served outside ``/js``. Exact match, so there is no path to traverse.
STATIC_FILES: dict[str, tuple[Path, str]] = {
    "/": (PROTOTYPE / "index.html", "text/html; charset=utf-8"),
    "/index.html": (PROTOTYPE / "index.html", "text/html; charset=utf-8"),
    "/repository.html": (PROTOTYPE / "repository.html", "text/html; charset=utf-8"),
    "/dependencies.html": (PROTOTYPE / "dependencies.html", "text/html; charset=utf-8"),
    "/compare.html": (PROTOTYPE / "compare.html", "text/html; charset=utf-8"),
    "/about": (PROTOTYPE / "about.html", "text/html; charset=utf-8"),
    "/about.html": (PROTOTYPE / "about.html", "text/html; charset=utf-8"),
    "/contact": (PROTOTYPE / "contact.html", "text/html; charset=utf-8"),
    "/contact.html": (PROTOTYPE / "contact.html", "text/html; charset=utf-8"),
    "/privacy": (PROTOTYPE / "privacy.html", "text/html; charset=utf-8"),
    "/privacy.html": (PROTOTYPE / "privacy.html", "text/html; charset=utf-8"),
    "/prototype.css": (PROTOTYPE / "prototype.css", "text/css; charset=utf-8"),
    "/tokens.css": (ROOT / "apps" / "web" / "src" / "styles" / "tokens.css", "text/css; charset=utf-8"),
    "/favicon.svg": (PROTOTYPE / "favicon.svg", "image/svg+xml"),
    "/favicon.ico": (PROTOTYPE / "favicon.ico", "image/x-icon"),
    "/og-image.png": (PROTOTYPE / "og-image.png", "image/png"),
    "/robots.txt": (PROTOTYPE / "robots.txt", "text/plain; charset=utf-8"),
    "/sitemap.xml": (PROTOTYPE / "sitemap.xml", "application/xml; charset=utf-8"),
    "/llms.txt": (PROTOTYPE / "llms.txt", "text/plain; charset=utf-8"),
    "/llms-full.txt": (PROTOTYPE / "llms-full.txt", "text/plain; charset=utf-8"),
    "/.well-known/llms.txt": (PROTOTYPE / "llms.txt", "text/plain; charset=utf-8"),
    "/schema.jsonld": (PROTOTYPE / "schema.jsonld", "application/ld+json; charset=utf-8"),
    "/site.webmanifest": (PROTOTYPE / "site.webmanifest", "application/manifest+json; charset=utf-8"),
}

#: Page-level Markdown representations returned when ``Accept: text/markdown`` is preferred.
MARKDOWN_PAGES: dict[str, str] = {
    "/": (
        "# VYRON — The Living Map of Technology\n\n"
        "> Open-source, provenance-first interactive technology atlas, live GitHub repository analyzer, "
        "PyPI/npm dependency graph & release history explorer, and side-by-side comparison tool.\n\n"
        "Status: early development (**not production ready**). The map shows a clearly labelled 60-entity "
        "development seed graph (`provenance: \"seed\"`), while Repository, Dependencies, and Compare read live "
        "public GitHub, PyPI, and npm APIs on demand (`provenance: \"sourced\"`).\n\n"
        "## Pages\n"
        "- [Interactive Technology Map](/): 60 seed entities, 68 relationships, 20 clusters, shortest-path finder, "
        "historical year slider, and JSON export.\n"
        "- [Live GitHub Repository Analyzer](/repository.html): stars, forks, license, language shares, opt-in "
        "contributors & latest release, and 52-week commit activity.\n"
        "- [PyPI & npm Package Dependencies & Releases](/dependencies.html): on-demand layered dependency graph "
        "plus registry release history timeline.\n"
        "- [Side-by-Side Compare](/compare.html): deterministic Shared vs. Only-A vs. Only-B dependency and "
        "language diffs.\n"
        "- [About](/about) | [Contact](/contact) | [Privacy](/privacy)\n\n"
        "## Agent & API Discovery\n"
        "- [llms.txt](/llms.txt) and [llms-full.txt](/llms-full.txt) — When-to-use guide & full API contract\n"
        "- [schema.jsonld](/schema.jsonld) — Schema.org structured data\n"
        "- [sitemap.xml](/sitemap.xml) — XML sitemap\n"
        "- API root: `/api/v1/meta`, `/api/v1/graph/overview`, `/api/v1/search?q=python`, "
        "`/api/v1/github/repositories/{owner}/{repo}`, `/api/v1/registries/{registry}/package?name=<pkg>`\n"
    ),
    "/repository.html": (
        "# Live GitHub Repository Analyzer — VYRON\n\n"
        "Inspect any public GitHub repository live via the GitHub REST API without storing user or repo data.\n\n"
        "- Browser URL: `/repository.html?repo=pallets/flask`\n"
        "- Core API: `GET /api/v1/github/repositories/{owner}/{repo}`\n"
        "- Opt-in Activity (contributors & latest release): "
        "`GET /api/v1/github/repositories/{owner}/{repo}/activity`\n"
        "- Opt-in 52-week Commit Activity: `GET /api/v1/github/repositories/{owner}/{repo}/commit-activity`\n\n"
        "See [/llms.txt](/llms.txt) for complete usage guidance.\n"
    ),
    "/dependencies.html": (
        "# PyPI & npm Package Dependency Graph & Release History — VYRON\n\n"
        "Read declared unconditional runtime dependencies one package at a time from PyPI or npm, with "
        "left-out conditional/dev/peer/optional declarations counted explicitly and opt-in release timelines.\n\n"
        "- Browser URL: `/dependencies.html?package=pypi:flask` or `/dependencies.html?package=npm:express`\n"
        "- Dependencies API: `GET /api/v1/registries/{registry}/package?name=<pkg>`\n"
        "- Releases API: `GET /api/v1/registries/{registry}/releases?name=<pkg>`\n\n"
        "See [/llms.txt](/llms.txt) for complete usage guidance.\n"
    ),
    "/compare.html": (
        "# Side-by-Side Package & GitHub Repository Compare — VYRON\n\n"
        "Compare two PyPI/npm packages or two public GitHub repositories live with deterministic Shared vs. "
        "Only-A vs. Only-B dependency or language diffs and zero subjective scores.\n\n"
        "- Packages URL: `/compare.html?mode=packages&a=pypi:flask&b=pypi:django`\n"
        "- Repositories URL: `/compare.html?mode=repos&a=pallets/flask&b=pallets/werkzeug`\n\n"
        "See [/llms.txt](/llms.txt) for complete usage guidance.\n"
    ),
    "/about": (
        "# About VYRON\n\n"
        "VYRON is an open-source (MIT), provenance-first interactive technology atlas and ecosystem explorer "
        "in early development (not production ready). Every fact carries an explicit provenance label "
        "(`sourced`, `estimated`, `inferred`, `predicted`, `user_generated`, `ai_generated`, or `seed`).\n\n"
        "- Repository: https://github.com/TejaPriyan/VYRON\n"
        "- Pages: [Map](/), [Repository](/repository.html), [Dependencies](/dependencies.html), "
        "[Compare](/compare.html), [Contact](/contact), [Privacy](/privacy)\n"
        "- Agent guide: [/llms.txt](/llms.txt)\n"
    ),
    "/contact": (
        "# Contact VYRON\n\n"
        "- GitHub Issues (bugs & features): https://github.com/TejaPriyan/VYRON/issues\n"
        "- Maintainer Email: teja1616150@gmail.com\n"
        "- Security Policy: https://github.com/TejaPriyan/VYRON/blob/main/SECURITY.md\n"
        "- Code of Conduct: https://github.com/TejaPriyan/VYRON/blob/main/CODE_OF_CONDUCT.md\n"
        "- Contributing Guide: https://github.com/TejaPriyan/VYRON/blob/main/docs/CONTRIBUTING.md\n"
    ),
    "/privacy": (
        "# VYRON Privacy & Data Handling\n\n"
        "- Zero browser storage: no `localStorage`, no `sessionStorage`, no tracking cookies, no third-party scripts.\n"
        "- Strict Content Security Policy (`default-src 'self'`, `script-src 'self'`, `frame-ancestors 'none'`).\n"
        "- All GitHub, PyPI, and npm API reads execute server-side with short ephemeral in-memory caching.\n"
        "- Questions: see [Contact](/contact).\n"
    ),
}
MARKDOWN_PAGES["/index.html"] = MARKDOWN_PAGES["/"]
MARKDOWN_PAGES["/about.html"] = MARKDOWN_PAGES["/about"]
MARKDOWN_PAGES["/contact.html"] = MARKDOWN_PAGES["/contact"]
MARKDOWN_PAGES["/privacy.html"] = MARKDOWN_PAGES["/privacy"]

SECURITY_HEADERS = (
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cache-Control", "no-store"),
)

StartResponse = Callable[..., Any]
WsgiApp = Callable[[dict[str, Any], StartResponse], Iterable[bytes]]


def _prefers_markdown(accept_header: str) -> bool:
    """Return True when the HTTP Accept header explicitly prefers text/markdown over text/html."""
    if not accept_header:
        return False
    md_q = -1.0
    html_q = -1.0
    for raw_part in accept_header.split(","):
        part = raw_part.strip()
        if not part:
            continue
        segments = [seg.strip() for seg in part.split(";")]
        media = segments[0].lower()
        q = 1.0
        for param in segments[1:]:
            if param.lower().startswith("q="):
                try:
                    q = float(param[2:].strip())
                except ValueError:
                    q = 0.0
        if media in ("text/markdown", "text/x-markdown"):
            md_q = max(md_q, q)
        elif media == "text/html":
            html_q = max(html_q, q)
    return md_q > 0.0 and md_q >= html_q


def _markdown_not_found(path: str) -> bytes:
    safe_path = path.replace("\r", "").replace("\n", "")[:200]
    body = (
        "# 404 — Page Not Found\n\n"
        f"The requested path `{safe_path}` does not exist on VYRON.\n\n"
        "## Available Pages & Discovery Resources\n"
        "- [VYRON Technology Map](/)\n"
        "- [Live GitHub Repository Analyzer](/repository.html)\n"
        "- [PyPI & npm Package Dependencies](/dependencies.html)\n"
        "- [Side-by-Side Compare](/compare.html)\n"
        "- [About](/about) | [Contact](/contact) | [Privacy](/privacy)\n"
        "- [LLM & Agent Guide (/llms.txt)](/llms.txt)\n"
        "- [XML Sitemap (/sitemap.xml)](/sitemap.xml)\n"
    )
    return body.encode("utf-8")


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

    def respond(
        start_response: StartResponse,
        status: str,
        body: bytes,
        content_type: str,
        head: bool,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> list[bytes]:
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            *SECURITY_HEADERS,
            *extra_headers,
        ]
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
        accept = str(environ.get("HTTP_ACCEPT", ""))
        wants_md = _prefers_markdown(accept)
        if path in MARKDOWN_PAGES and wants_md:
            md_body = MARKDOWN_PAGES[path].encode("utf-8")
            extra = (("Vary", "Accept"), ("Link", DISCOVERY_LINK_HEADER))
            return respond(start_response, "200 OK", md_body, "text/markdown; charset=utf-8", head, extra)
        target: tuple[Path, str] | None = None
        if path in STATIC_FILES:
            target = STATIC_FILES[path]
        elif path.startswith("/js/"):
            script = _resolve_script(path, dist)
            if script is not None:
                target = (script, "text/javascript; charset=utf-8")
        if target is None:
            if wants_md:
                return respond(
                    start_response,
                    "404 Not Found",
                    _markdown_not_found(path),
                    "text/markdown; charset=utf-8",
                    head,
                    (("Vary", "Accept"),),
                )
            note = b"Not found.\n"
            return respond(
                start_response, "404 Not Found", note, "text/plain; charset=utf-8", head, (("Vary", "Accept"),)
            )
        file, content_type = target
        if not file.is_file():
            hint = b"Missing build output. Run: python3 scripts/build_prototype.py\n"
            return respond(start_response, "404 Not Found", hint, "text/plain; charset=utf-8", head)
        extra = (("Vary", "Accept"), ("Link", DISCOVERY_LINK_HEADER)) if path in MARKDOWN_PAGES else ()
        return respond(start_response, "200 OK", file.read_bytes(), content_type, head, extra)

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
