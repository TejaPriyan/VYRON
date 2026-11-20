"""Exercises the real urllib transport against a local HTTP server (no external network)."""

import contextlib
import io
import json
import ssl
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from vyron_api.adapters.memory_cache import InMemoryTTLCache
from vyron_api.github.conditional import cache_key, cached_get
from vyron_api.github.retry import send_with_retry
from vyron_api.github.transport import UrllibGitHubTransport
from vyron_api.providers.github import (
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)

REQUESTS: list[dict[str, object]] = []


class QuietServer(ThreadingHTTPServer):
    """A client that hangs up before the answer is written (these tests do, on purpose) is not a test failure.

    Without this, the standard server prints a traceback for each such hang-up (``ConnectionAbortedError`` on
    Windows, ``BrokenPipeError`` elsewhere) in the middle of a passing run. Any other error is still reported.
    """

    def handle_error(self, request: object, client_address: object) -> None:
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)  # type: ignore[arg-type]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # keep test output quiet
        return

    def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        REQUESTS.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
        if self.path == "/repos/a/b":
            if self.headers.get("If-None-Match") == '"v1"':
                self._send(304, headers={"x-ratelimit-remaining": "4998"})
            else:
                self._send(
                    200,
                    json.dumps({"full_name": "a/b"}).encode(),
                    {"ETag": '"v1"', "X-RateLimit-Remaining": "4999", "Content-Type": "application/json"},
                )
        elif self.path == "/missing":
            self._send(404, b'{"message": "Not Found"}')
        elif self.path == "/limited":
            self._send(
                403,
                b'{"message": "API rate limit exceeded"}',
                {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 5000)},
            )
        elif self.path == "/redirect":
            self._send(301, headers={"Location": f"http://127.0.0.1:{self.server.server_port}/other"})
        elif self.path == "/other":
            self._send(200, b"{}")
        elif self.path == "/big":
            self._send(200, b"x" * 1000)
        elif self.path == "/slow":
            time.sleep(1.0)
            self._send(200, b"{}")
        elif self.path == "/truncated":
            # Promises 100 bytes, sends 12 and hangs up, with a validator a cache would trust.
            self.send_response(200)
            self.send_header("ETag", '"cut"')
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"full_name"')
            self.close_connection = True
        elif self.path == "/garbage":
            self.wfile.write(b"this is not an HTTP response\r\n\r\n")
            self.close_connection = True
        elif self.path == "/not-modified-with-length":
            self.send_response(304)  # a 304 may legitimately carry the Content-Length of the body it stands for
            self.send_header("ETag", '"v1"')
            self.send_header("Content-Length", "4096")
            self.end_headers()
        else:
            self._send(500, b"{}")


class TransportTest(unittest.IsolatedAsyncioTestCase):
    server: ThreadingHTTPServer
    thread: threading.Thread
    base: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = QuietServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        REQUESTS.clear()

    def transport(self, **kwargs: object) -> UrllibGitHubTransport:
        options: dict[str, object] = {"base_url": self.base, "allow_local_test_server": True}
        options.update(kwargs)
        return UrllibGitHubTransport(**options)  # type: ignore[arg-type]

    async def test_sends_documented_headers_and_hides_the_token(self) -> None:
        transport = self.transport(token="ghp_secret_value", user_agent="vyron-test/1")
        response = await transport.get("/repos/a/b")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json(), {"full_name": "a/b"})
        sent = REQUESTS[0]["headers"]
        assert isinstance(sent, dict)
        self.assertEqual(sent["authorization"], "Bearer ghp_secret_value")
        self.assertEqual(sent["accept"], "application/vnd.github+json")
        self.assertEqual(sent["user-agent"], "vyron-test/1")
        self.assertEqual(sent["x-github-api-version"], "2022-11-28")
        self.assertNotIn("ghp_secret_value", repr(transport))
        self.assertTrue(transport.authenticated)
        self.assertEqual(response.headers["etag"], '"v1"')

    async def test_unauthenticated_requests_send_no_authorization(self) -> None:
        await self.transport().get("/repos/a/b")
        sent = REQUESTS[0]["headers"]
        assert isinstance(sent, dict)
        self.assertNotIn("authorization", sent)

    async def test_error_statuses_are_returned_not_raised(self) -> None:
        self.assertEqual((await self.transport().get("/missing")).status, 404)

    async def test_redirects_are_not_followed(self) -> None:
        response = await self.transport(token="t").get("/redirect")
        self.assertEqual(response.status, 301)
        self.assertEqual([r["path"] for r in REQUESTS], ["/redirect"])

    async def test_oversized_bodies_are_refused(self) -> None:
        with mock.patch("vyron_api.github.transport.MAX_BODY_BYTES", 100):
            with self.assertRaises(GitHubError):
                await self.transport().get("/big")

    async def test_timeouts_surface_as_oserror_for_the_retry_layer(self) -> None:
        with self.assertRaises(OSError):
            await self.transport(timeout=0.2).get("/slow")

    async def test_a_body_cut_short_is_a_network_failure_not_a_response(self) -> None:
        with self.assertRaises(ConnectionError):
            await self.transport().get("/truncated")

    async def test_a_cut_short_body_is_retried_and_never_cached(self) -> None:
        transport = self.transport()
        cache = InMemoryTTLCache()
        key = cache_key("/truncated")

        async def send(headers: dict[str, str]):  # type: ignore[no-untyped-def]
            return await transport.get("/truncated", headers)

        async def no_sleep(_: float) -> None:
            return None

        with self.assertRaises(GitHubUnavailableError):
            await cached_get(cache, key, send, max_retries=1, sleep=no_sleep)
        self.assertEqual(len(REQUESTS), 2)  # tried, retried
        self.assertIsNone(await cache.get(key))  # nothing a later 304 could keep serving

    async def test_a_response_that_is_not_http_is_a_network_failure(self) -> None:
        with self.assertRaises(ConnectionError):
            await self.transport().get("/garbage")

    async def test_a_304_that_declares_a_length_is_not_mistaken_for_a_cut_short_body(self) -> None:
        response = await self.transport().get("/not-modified-with-length", {"if-none-match": '"v1"'})
        self.assertEqual((response.status, response.body), (304, b""))

    async def test_retry_and_cache_end_to_end_over_real_sockets(self) -> None:
        transport = self.transport()
        cache = InMemoryTTLCache()
        key = cache_key("/repos/a/b")

        async def send(headers: dict[str, str]):  # type: ignore[no-untyped-def]
            return await transport.get("/repos/a/b", headers)

        first = await cached_get(cache, key, send)
        second = await cached_get(cache, key, send)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(second.response.json(), {"full_name": "a/b"})
        sent = REQUESTS[1]["headers"]
        assert isinstance(sent, dict)
        self.assertEqual(sent["if-none-match"], '"v1"')

    async def test_typed_errors_over_real_sockets(self) -> None:
        transport = self.transport()

        async def missing(headers: dict[str, str]):  # type: ignore[no-untyped-def]
            return await transport.get("/missing", headers)

        async def limited(headers: dict[str, str]):  # type: ignore[no-untyped-def]
            return await transport.get("/limited", headers)

        with self.assertRaises(GitHubNotFoundError):
            await send_with_retry(missing)
        with self.assertRaises(GitHubRateLimitedError):
            await send_with_retry(limited)

    def test_constructor_validation(self) -> None:
        UrllibGitHubTransport()  # default is https://api.github.com
        for kwargs in (
            {"base_url": "https://evil.example"},
            {"base_url": "http://api.github.com"},
            {"base_url": "https://api.github.com/extra"},
            {"base_url": "https://user:pw@api.github.com"},
            {"base_url": f"{self.base}"},  # local host without the explicit test flag
            {"user_agent": "  "},
            {"timeout": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                UrllibGitHubTransport(**kwargs)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):  # test flag only unlocks loopback hosts
            UrllibGitHubTransport(base_url="https://evil.example", allow_local_test_server=True)

    def test_a_token_that_could_not_be_a_header_value_is_refused_without_being_echoed(self) -> None:
        for bad in ("two words", "line\nbreak", "tab\there", "tökén", "x" * 256, "bad\r\nX-Evil: 1"):
            with self.subTest(bad=bad[:10]), self.assertRaises(ValueError) as caught:
                UrllibGitHubTransport(token=bad)
            self.assertNotIn(bad, str(caught.exception))
        for good in ("ghp_" + "a" * 36, "github_pat_" + "B1_" * 20, "x"):
            UrllibGitHubTransport(token=good)
        UrllibGitHubTransport(token="")  # blank means unauthenticated, as for a missing variable
        self.assertFalse(UrllibGitHubTransport(token=None).authenticated)

    def test_tls_verification_is_required_and_wired_into_the_opener(self) -> None:
        transport = UrllibGitHubTransport()
        context = transport._context
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        handlers = [h for h in transport._opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
        self.assertEqual(len(handlers), 1, "exactly one HTTPS handler")
        self.assertIs(handlers[0]._context, context, "the opener must use the verifying context")

    def test_url_for_only_accepts_api_paths_on_the_configured_host(self) -> None:
        transport = UrllibGitHubTransport()
        self.assertEqual(transport.url_for("/repos/a/b?per_page=100"), "https://api.github.com/repos/a/b?per_page=100")
        self.assertEqual(
            transport.url_for("https://api.github.com/repos/a/b?page=2"),
            "https://api.github.com/repos/a/b?page=2",
        )
        for bad in (
            "repos/a/b",
            "//evil.example/x",
            "/repos/../admin",
            "/repos/a/..",
            "/a b",
            "/x#frag",
            "https://evil.example/x",
            "https://api.github.com.evil.example/x",
            "http://api.github.com/x",
            "/é",
            "",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                transport.url_for(bad)


class QuietServerTest(unittest.TestCase):
    """The test server stays silent about a client hanging up and speaks up about anything else."""

    @staticmethod
    def reported(error: Exception) -> str:
        server = QuietServer(("127.0.0.1", 0), Handler)
        try:
            captured = io.StringIO()
            try:
                raise error
            except Exception:
                with contextlib.redirect_stderr(captured):
                    server.handle_error(None, ("127.0.0.1", 1))
            return captured.getvalue()
        finally:
            server.server_close()

    def test_a_hang_up_is_not_reported(self) -> None:
        for error in (ConnectionAbortedError(), ConnectionResetError(), BrokenPipeError()):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(self.reported(error), "")

    def test_any_other_error_still_is(self) -> None:
        self.assertIn("ValueError", self.reported(ValueError("a real fault in the handler")))


if __name__ == "__main__":
    unittest.main()
