"""A local stand-in for pypi.org and registry.npmjs.org, so the package route can be exercised without reaching them.

    python3 scripts/standin_registry.py --mode api --port 0         # the API wired to the stand-in
    python3 scripts/standin_registry.py --mode prototype --port 0   # the prototype wired to it

EVERYTHING THIS SERVES IS FIXTURE DATA, NOT DATA FROM PYPI OR NPM. Every package is named ``fixture-*`` (or sits in the
``@fixture-scope`` scope), and its description says it is made up, so a screenshot taken against this server labels
itself. The answers have the shape of a registry's, written from its documentation and from the real answers reported
for flask and express (see ``apps/api/tests/registry_fakes.py``, the one place they are defined), so a green run here
shows that VYRON handles those shapes, not that a registry sends them. ``scripts/capture_registry.py`` records what the
real registries send.

The two registries are served under two prefixes of one loopback server: ``/pypi-host/pypi/<name>/json`` and
``/npm-host/<name>``. Each package name is a scenario (see ``PYPI_SCENARIOS`` and ``NPM_SCENARIOS``): a name that is
not listed answers 404. ``/__standin/requests`` lists what was asked and ``/__standin/reset`` clears the list, so a
test can check that a second look did not reach the registry. It listens on loopback only.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
for relative in ("apps/api/src", "apps/api/tests", "apps/web/prototype"):
    if str(ROOT / relative) not in sys.path:
        sys.path.insert(0, str(ROOT / relative))

from registry_fakes import NPM_SCENARIOS, PYPI_SCENARIOS, npm_fixture, pypi_fixture  # noqa: E402

PYPI_PREFIX = "/pypi-host"
NPM_PREFIX = "/npm-host"


class _Handler(BaseHTTPRequestHandler):
    server: StandIn

    def log_message(self, *args: object) -> None:
        return

    def _send(self, status: int, body: bytes = b"", content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload).encode())

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path == "/__standin/requests":
            return self._json(200, self.server.log_copy())
        if path == "/__standin/reset":
            self.server.reset()
            return self._json(200, {"reset": True})
        self.server.record(path)
        if path.startswith(PYPI_PREFIX + "/pypi/") and path.endswith("/json"):
            return self._pypi(path[len(PYPI_PREFIX + "/pypi/") : -len("/json")])
        if path.startswith(NPM_PREFIX + "/"):
            return self._npm(path[len(NPM_PREFIX) + 1 :])
        self._json(404, {"message": "Not Found"})

    def _pypi(self, name: str) -> None:
        if name == "fixture-limited":
            return self._send(429, b'{"message": "fixture: slow down"}')
        if name == "fixture-broken":
            return self._send(500, b'{"message": "fixture: broken"}')
        if name == "fixture-garbled":
            return self._send(200, b"this is not json")
        payload = pypi_fixture(name)
        self._json(200, payload) if payload is not None else self._json(404, {"message": "Not Found"})

    def _npm(self, name: str) -> None:
        payload = npm_fixture(name)
        self._json(200, payload) if payload is not None else self._json(404, {"error": "Not found"})


class StandIn(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        super().__init__((host, port), _Handler)
        self._lock = threading.Lock()
        self._log: list[str] = []

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    @property
    def pypi_base(self) -> str:
        return self.base + PYPI_PREFIX

    @property
    def npm_base(self) -> str:
        return self.base + NPM_PREFIX

    def record(self, path: str) -> None:
        with self._lock:
            self._log.append(path)

    def log_copy(self) -> list[str]:
        with self._lock:
            return list(self._log)

    def reset(self) -> None:
        with self._lock:
            self._log.clear()


def start_standin(host: str = "127.0.0.1", port: int = 0) -> StandIn:
    """Start the stand-in on a background thread; call ``shutdown()`` and ``server_close()`` to stop it."""
    server = StandIn(host, port)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    from wsgiref.simple_server import make_server

    from vyron_api.api.bootstrap import build_seed_application
    from vyron_api.api.hostcheck import LOOPBACK_HOSTS
    from vyron_api.api.wsgi import make_dev_server
    from vyron_api.registries.provider import build_live_package_provider

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("api", "prototype"), default="api")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-rate-limit", action="store_true", help="turn VYRON's own rate limiter off")
    args = parser.parse_args(argv)

    standin = start_standin()
    provider = build_live_package_provider(
        pypi_base_url=standin.pypi_base, npm_base_url=standin.npm_base, allow_local_test_server=True
    )
    application = build_seed_application(
        packages=provider, allowed_hosts=LOOPBACK_HOSTS, rate_limit=not args.no_rate_limit
    )
    print(f"Registry stand-in (FIXTURE DATA, NOT FROM PYPI OR NPM): {standin.base}", flush=True)
    print(f"Packages it knows: {', '.join(sorted(set(PYPI_SCENARIOS) | set(NPM_SCENARIOS)))}", flush=True)
    if args.mode == "api":
        server = make_dev_server(application, "127.0.0.1", args.port)
        print(f"Listening on http://127.0.0.1:{server.server_port}/api/v1/meta", flush=True)
    else:
        import serve  # apps/web/prototype/serve.py

        wsgi = serve.make_app(application=application)
        server = make_server("127.0.0.1", args.port, wsgi, handler_class=serve._QuietHandler)
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
