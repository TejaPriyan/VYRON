"""A local stand-in for api.github.com, so the live GitHub path can be exercised without reaching GitHub.

    python3 scripts/standin_github.py --mode api --port 0         # the API wired to the stand-in
    python3 scripts/standin_github.py --mode prototype --port 0   # the map prototype wired to it

EVERYTHING THIS SERVES IS FIXTURE DATA, NOT DATA FROM GITHUB. The repositories belong to a made-up
organisation (``fixture-org``) and their descriptions say so, so a screenshot taken against this server
labels itself. The payloads are written from GitHub's documentation (see ``apps/api/tests/github_fakes.py``,
which is the one place they are defined), so a green run here shows that VYRON handles those shapes, not that
GitHub sends them. ``scripts/live_github_check.py`` is the check against the real thing.

Each repository name is a scenario: ``example-repo`` is a healthy one; the others answer the way GitHub
answers when a repository is missing, private, renamed, rate limited, refused by a gateway, broken or garbled,
and (for the opt-in contributors and latest-release read) when the contributor list is empty, refused, rate limited
or garbled and when there is no release. The answers for contributors and releases are written from GitHub's
documentation too; those were later seen from GitHub (see docs/GITHUB.md). The commit-activity answers (202 and an
empty object while GitHub computes, then 200 with 52 weeks) follow what GitHub really sent on 2026-11-04, with made-up
counts; the 204 is written from the documentation and has not been seen.

The stand-in also answers ``/__standin/requests`` (what it was asked, with the Authorization header reduced to
whether one was sent) and ``/__standin/reset``, so a test can check that a second click did not reach GitHub.
It listens on loopback only.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for relative in ("apps/api/src", "apps/api/tests", "apps/web/prototype"):
    if str(ROOT / relative) not in sys.path:
        sys.path.insert(0, str(ROOT / relative))

from github_fakes import (  # noqa: E402
    commit_activity_payload,
    contributors_payload,
    languages_payload,
    rate_limit_payload,
    release_payload,
    repo_payload,
)

ORG = "fixture-org"
LIMIT = 60
WINDOW_SECONDS = 3600

#: Every repository the stand-in knows, and the one line saying what it is for.
SCENARIOS: dict[str, str] = {
    "example-repo": "a healthy public repository",
    "quiet-repo": "archived, no license, topics, description or languages",
    "private-repo": "GitHub says it is private (VYRON must treat it as not found)",
    "moved-repo": "answers 301: the repository was renamed",
    "missing-repo": "answers 404",
    "limited-repo": "answers 403 with the rate limit used up and a reset minutes away",
    "gateway-repo": "answers 403 'No linked GitHub account', as the build sandbox's gateway did",
    "broken-repo": "answers 500 every time",
    "flaky-repo": "answers 503 once, then succeeds (the retry)",
    "garbled-repo": "answers 200 with a body that is not JSON",
    "huge-repo": "healthy, but GitHub refuses (403) to list its contributors, as documented for huge histories",
    "unreleased-repo": "healthy, with contributors but no published release (404 on 'latest release')",
    "burst-repo": "healthy, but the contributor list answers 403 with the rate limit used up",
    "garbled-list-repo": "healthy, but the contributor list is an object instead of a list",
    "other-license-repo": "healthy, but its license is GitHub's 'Other' with no SPDX id (torvalds/linux, 2026-11-02)",
    "slow-stats-repo": "healthy, but its commit activity answers 202 (still being computed) every time",
}
#: How many times the commit-activity answer is 202 before the first 200, as GitHub's documentation describes.
STATS_PENDING_ANSWERS = 2
#: These never get as far as the opt-in read, because the repository read itself fails first.
_REPOSITORY_FAILS_FIRST = frozenset(
    {"missing-repo", "moved-repo", "limited-repo", "gateway-repo", "broken-repo", "garbled-repo", "flaky-repo"}
)


def name_unknown(name: str) -> bool:
    return name not in SCENARIOS


class _Handler(BaseHTTPRequestHandler):
    server: StandIn

    def log_message(self, *args: object) -> None:
        return

    # ---- helpers ------------------------------------------------------------------------------
    def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        merged = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
        self._send(status, json.dumps(payload).encode(), merged)

    def _quota_headers(self, remaining: int) -> dict[str, str]:
        return {
            "x-ratelimit-limit": str(LIMIT),
            "x-ratelimit-remaining": str(max(0, remaining)),
            "x-ratelimit-used": str(LIMIT - max(0, remaining)),
            "x-ratelimit-reset": str(int(self.server.window_end)),
            "x-ratelimit-resource": "core",
        }

    # ---- routing ------------------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path, _, query = self.path.partition("?")
        if path == "/__standin/requests":
            return self._json(200, self.server.log_copy())
        if path == "/__standin/reset":
            self.server.reset()
            return self._json(200, {"reset": True})
        sent_auth = "authorization" in {name.lower() for name in self.headers}
        if_none_match = self.headers.get("If-None-Match")
        self.server.record(path, sent_auth, if_none_match, query)
        if path == "/rate_limit":  # free: not counted against the quota
            used = self.server.core_requests()
            payload = rate_limit_payload(limit=LIMIT, remaining=LIMIT - used, reset=int(self.server.window_end))
            return self._json(200, payload)
        remaining = LIMIT - self.server.core_requests()
        parts = path.strip("/").lower().split("/")
        if len(parts) < 3 or parts[0] != "repos" or parts[1] != ORG or name_unknown(parts[2]):
            return self._json(404, {"message": "Not Found"}, self._quota_headers(remaining))
        name, rest = parts[2], parts[3:]
        quota = self._quota_headers(remaining)
        if not rest:
            return self._repository(name, quota, if_none_match)
        if rest == ["languages"]:
            return self._languages(name, quota, if_none_match)
        if rest == ["contributors"]:
            return self._contributors(name, quota, if_none_match)
        if rest == ["releases", "latest"]:
            return self._latest_release(name, quota, if_none_match)
        if rest == ["stats", "commit_activity"]:
            return self._commit_activity(name, quota, if_none_match)
        return self._json(404, {"message": "Not Found"}, quota)

    def _repository(self, name: str, quota: dict[str, str], if_none_match: str | None) -> None:
        if name == "missing-repo":
            return self._json(404, {"message": "Not Found"}, quota)
        if name == "moved-repo":
            location = f"{self.server.base}/repositories/1296269"
            return self._json(301, {"message": "Moved Permanently"}, {**quota, "Location": location})
        if name == "limited-repo":
            limited = {**quota, "x-ratelimit-remaining": "0", "x-ratelimit-used": str(LIMIT)}
            limited["x-ratelimit-reset"] = str(int(time.time()) + 180)
            return self._json(403, {"message": "API rate limit exceeded for 127.0.0.1."}, limited)
        if name == "gateway-repo":
            return self._json(403, {"message": "No linked GitHub account. Connect your GitHub account and retry."})
        if name == "broken-repo":
            return self._json(500, {"message": "Server Error"}, quota)
        if name == "flaky-repo" and self.server.first_time("flaky"):
            return self._json(503, {"message": "Service Unavailable"}, quota)
        if name == "garbled-repo":
            return self._send(200, b"<html>not json</html>", {**quota, "ETag": '"garbled"'})
        etag = f'"fixture-{name}"'
        if if_none_match == etag:
            return self._send(304, b"", {**quota, "ETag": etag})
        overrides: dict[str, Any] = {
            "name": name,
            "full_name": f"{ORG}/{name}",
            "html_url": f"https://github.com/{ORG}/{name}",
            "url": f"{self.server.base}/repos/{ORG}/{name}",
        }
        if name == "private-repo":
            overrides.update(private=True, visibility="private")
        if name == "other-license-repo":
            overrides["license"] = {
                "key": "other", "name": "Other", "node_id": "MDc6TGljZW5zZTA=", "spdx_id": "NOASSERTION", "url": None,
            }  # the shape GitHub really sent for torvalds/linux, see apps/api/tests/fixtures/github_real
        if name == "quiet-repo":
            overrides.update(
                archived=True, license=None, topics=[], description=None, language=None,
                stargazers_count=0, forks_count=0, open_issues_count=0, pushed_at=None,
            )  # fmt: skip
        self._json(200, repo_payload(**overrides), {**quota, "ETag": etag})

    def _languages(self, name: str, quota: dict[str, str], if_none_match: str | None) -> None:
        if name in ("broken-repo", "missing-repo", "limited-repo"):
            return self._json(500, {"message": "Server Error"}, quota)
        etag = f'"fixture-{name}-languages"'
        if if_none_match == etag:
            return self._send(304, b"", {**quota, "ETag": etag})
        body = {} if name == "quiet-repo" else languages_payload()
        self._json(200, body, {**quota, "ETag": etag})


    def _contributors(self, name: str, quota: dict[str, str], if_none_match: str | None) -> None:
        if name == "quiet-repo":
            return self._send(204, b"", quota)  # GitHub's documented answer for a repository with nothing to list
        if name == "huge-repo":
            message = "The history or contributor list is too large to list contributors for this repository"
            return self._json(403, {"message": message + " via the API."}, quota)
        if name == "burst-repo":
            limited = {**quota, "x-ratelimit-remaining": "0", "x-ratelimit-used": str(LIMIT)}
            limited["x-ratelimit-reset"] = str(int(time.time()) + 180)
            return self._json(403, {"message": "API rate limit exceeded for 127.0.0.1."}, limited)
        if name in _REPOSITORY_FAILS_FIRST:
            return self._json(404, {"message": "Not Found"}, quota)
        etag = f'"fixture-{name}-contributors"'
        if if_none_match == etag:
            return self._send(304, b"", {**quota, "ETag": etag})
        body: Any = {"message": "this is not a list"} if name == "garbled-list-repo" else contributors_payload()
        self._json(200, body, {**quota, "ETag": etag})

    def _latest_release(self, name: str, quota: dict[str, str], if_none_match: str | None) -> None:
        if name in ("quiet-repo", "unreleased-repo") or name in _REPOSITORY_FAILS_FIRST:
            return self._json(404, {"message": "Not Found"}, quota)
        etag = f'"fixture-{name}-release"'
        if if_none_match == etag:
            return self._send(304, b"", {**quota, "ETag": etag})
        page = f"https://github.com/{ORG}/{name}/releases/tag/v1.2.3"
        self._json(200, release_payload(html_url=page), {**quota, "ETag": etag})


    def _commit_activity(self, name: str, quota: dict[str, str], if_none_match: str | None) -> None:
        """The last year of commit activity, answered the way GitHub really answered on 2026-11-04: 202 with the body
        ``{}`` while it computes (twice here; ``slow-stats-repo`` never finishes), then 200 with a weak ETag and a
        one-minute ``cache-control``. The counts are made up. The 204 for a repository with nothing to count is
        written from the documentation and has not been seen from GitHub."""
        if name in _REPOSITORY_FAILS_FIRST:
            return self._json(404, {"message": "Not Found"}, quota)
        if name == "quiet-repo":
            return self._send(204, b"", quota)
        if name == "slow-stats-repo" or self.server.times_asked(f"commit-activity-{name}") <= STATS_PENDING_ANSWERS:
            return self._json(202, {}, quota)
        etag = f'W/"fixture-{name}-commit-activity"'
        if if_none_match == etag:
            return self._send(304, b"", {**quota, "ETag": etag})
        headers = {**quota, "ETag": etag, "Cache-Control": "public, max-age=60, s-maxage=60"}
        self._json(200, commit_activity_payload(), headers)


class StandIn(ThreadingHTTPServer):
    """The server, with the request log and the (fixed) rate-limit window."""

    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        super().__init__((host, port), _Handler)
        self._lock = threading.Lock()
        self._log: list[dict[str, Any]] = []
        self._seen: set[str] = set()
        self._asked: dict[str, int] = {}
        self.window_end = time.time() + WINDOW_SECONDS

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def record(self, path: str, authenticated: bool, if_none_match: str | None, query: str = "") -> None:
        with self._lock:
            self._log.append(
                {"path": path, "query": query, "authenticated": authenticated, "if_none_match": if_none_match}
            )

    def log_copy(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._log)

    def core_requests(self) -> int:
        with self._lock:
            return sum(1 for entry in self._log if entry["path"] != "/rate_limit")

    def first_time(self, key: str) -> bool:
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            return True

    def times_asked(self, key: str) -> int:
        """Counts this question and says how many times it has now been asked (1 the first time)."""
        with self._lock:
            self._asked[key] = self._asked.get(key, 0) + 1
            return self._asked[key]

    def reset(self) -> None:
        with self._lock:
            self._log.clear()
            self._seen.clear()
            self._asked.clear()


def start_standin(host: str = "127.0.0.1", port: int = 0) -> StandIn:
    """Start the stand-in on a background thread; call ``shutdown()`` and ``server_close()`` to stop it."""
    server = StandIn(host, port)
    # A short poll interval makes shutdown() return promptly instead of after serve_forever's default half second.
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    from wsgiref.simple_server import make_server

    from vyron_api.api.bootstrap import build_seed_application
    from vyron_api.api.hostcheck import LOOPBACK_HOSTS
    from vyron_api.api.wsgi import make_dev_server
    from vyron_api.github.provider import build_live_provider

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("api", "prototype"), default="api")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-rate-limit", action="store_true", help="turn VYRON's own rate limiter off")
    args = parser.parse_args(argv)

    standin = start_standin()
    provider = build_live_provider(base_url=standin.base, allow_local_test_server=True)
    application = build_seed_application(
        github=provider, allowed_hosts=LOOPBACK_HOSTS, rate_limit=not args.no_rate_limit
    )
    print(f"GitHub stand-in (FIXTURE DATA, NOT FROM GITHUB): {standin.base}", flush=True)
    if args.mode == "api":
        server = make_dev_server(application, "127.0.0.1", args.port)
        print(f"Listening on http://127.0.0.1:{server.server_port}/api/v1/meta", flush=True)
    else:
        import serve  # apps/web/prototype/serve.py

        wsgi = serve.make_app(application=application)
        server = make_server("127.0.0.1", args.port, wsgi, handler_class=serve._QuietHandler)  # noqa: SLF001
        print(f"Listening on http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        standin.shutdown()
        standin.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
