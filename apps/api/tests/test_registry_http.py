"""The shared registry reader: what it refuses, what it reports, and that it never repeats a registry's words."""

import http.server
import threading
import unittest
import urllib.error
from unittest import mock

from registry_fakes import FakeResponse, FakeTransport
from vyron_api.registries import http as registry_http
from vyron_api.registries.errors import PackageNotFoundError, RegistryError
from vyron_api.registries.http import USER_AGENT, fetch_json, text_field

URL = "https://pypi.org/pypi/flask/json"


def read(transport: FakeTransport, url: str = URL, host: str = "pypi.org") -> object:
    return fetch_json(url, host=host, what="PyPI", transport=transport)


class Reading(unittest.TestCase):
    def test_a_good_answer_is_parsed(self) -> None:
        self.assertEqual(read(FakeTransport({URL: b'{"a": 1}'})), {"a": 1})

    def test_it_sends_a_user_agent_with_no_address_and_no_credentials(self) -> None:
        transport = FakeTransport({URL: b"{}"})
        read(transport)
        headers = transport.requested_headers[0]
        self.assertEqual(headers["user-agent"], USER_AGENT)
        self.assertNotIn("http", USER_AGENT)
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)

    def test_other_hosts_and_schemes_are_refused_before_any_request(self) -> None:
        transport = FakeTransport({})
        for url in ("http://pypi.org/pypi/flask/json", "https://evil.example/pypi/flask/json",
                    "https://pypi.org.evil.example/x", "https://user:pw@pypi.org/x", "file:///etc/passwd"):  # fmt: skip
            with self.subTest(url=url), self.assertRaises(RegistryError):
                read(transport, url)
        self.assertEqual(transport.requested_urls, [])


class LocalTestServer(unittest.TestCase):
    LOCAL = "http://127.0.0.1:9/pypi-host/pypi/flask/json"

    def test_plain_http_to_loopback_is_read_only_when_a_stand_in_is_said_to_be_in_use(self) -> None:
        transport = FakeTransport({self.LOCAL: b'{"a": 1}'})
        allowed = fetch_json(
            self.LOCAL, host="pypi.org", what="PyPI", transport=transport, allow_local_test_server=True
        )
        self.assertEqual(allowed, {"a": 1})
        with self.assertRaises(RegistryError):
            fetch_json(self.LOCAL, host="pypi.org", what="PyPI", transport=transport)

    def test_the_allowance_covers_loopback_only_and_no_credentials(self) -> None:
        transport = FakeTransport({})
        for url in ("http://example.com/x", "http://user:pw@127.0.0.1:9/x", "http://127.0.0.1.evil.example/x",
                    "ftp://127.0.0.1/x"):  # fmt: skip
            with self.subTest(url=url), self.assertRaises(RegistryError):
                fetch_json(url, host="pypi.org", what="PyPI", transport=transport, allow_local_test_server=True)
        self.assertEqual(transport.requested_urls, [])


class Failing(unittest.TestCase):
    def test_not_found_rate_limited_and_server_errors_have_their_own_fixed_messages(self) -> None:
        with self.assertRaises(PackageNotFoundError) as caught:
            read(FakeTransport({URL: 404}))
        self.assertEqual(str(caught.exception), "PyPI has no such package")
        with self.assertRaises(RegistryError) as limited:
            read(FakeTransport({URL: 429}))
        self.assertIn("limiting requests", str(limited.exception))
        with self.assertRaises(RegistryError) as unavailable:
            read(FakeTransport({URL: 503}))
        self.assertEqual(str(unavailable.exception), "PyPI answered HTTP 503")

    def test_a_redirect_is_reported_and_not_followed(self) -> None:
        for status in (301, 302, 307, 308):
            with self.subTest(status=status), self.assertRaises(RegistryError) as caught:
                read(FakeTransport({URL: status}))
            self.assertIn("redirect", str(caught.exception))

    def test_a_registrys_own_words_never_reach_the_message(self) -> None:
        for outcome in (500, 404, 429, urllib.error.URLError("secret detail"), OSError("secret detail"),
                        TimeoutError("secret detail"), b"secret detail", b"\xff\xfe"):  # fmt: skip
            with self.subTest(outcome=repr(outcome)), self.assertRaises(RegistryError) as caught:
                read(FakeTransport({URL: outcome}))
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("upstream words", str(caught.exception))
            self.assertNotIn("pypi.org/pypi", str(caught.exception))

    def test_network_failures_are_one_message(self) -> None:
        for error in (urllib.error.URLError("x"), ConnectionRefusedError(), TimeoutError()):
            with self.subTest(error=type(error).__name__), self.assertRaises(RegistryError) as caught:
                read(FakeTransport({URL: error}))
            self.assertEqual(str(caught.exception), "could not reach PyPI")

    def test_invalid_json_and_invalid_utf8_are_refused(self) -> None:
        for body in (b"not json", b"", b"\xff\xfe\x00"):
            with self.subTest(body=body), self.assertRaises(RegistryError) as caught:
                read(FakeTransport({URL: body}))
            self.assertEqual(str(caught.exception), "PyPI did not send valid JSON")

    def test_a_body_over_the_cap_is_an_error_not_a_truncated_parse(self) -> None:
        with mock.patch.object(registry_http, "MAX_RESPONSE_BYTES", 20):
            self.assertEqual(read(FakeTransport({URL: b'{"a": "' + b"x" * 5 + b'"}'})), {"a": "xxxxx"})
            with self.assertRaises(RegistryError) as caught:
                read(FakeTransport({URL: b'{"a": "' + b"x" * 50 + b'"}'}))
            self.assertIn("sent more than", str(caught.exception))

    def test_the_reader_asks_for_one_byte_more_than_the_cap_so_it_can_tell(self) -> None:
        seen: list[int | None] = []

        class Spy(FakeResponse):
            def read(self, amount: int | None = None) -> bytes:
                seen.append(amount)
                return super().read(amount)

        class Transport:
            def open(self, req: object, timeout: float = 10.0) -> Spy:
                return Spy(b"{}")

        fetch_json(URL, host="pypi.org", what="PyPI", transport=Transport())
        self.assertEqual(seen, [registry_http.MAX_RESPONSE_BYTES + 1])


class Redirects(unittest.TestCase):
    """The real opener, against a local server, because the refusal lives in a urllib handler."""

    def test_the_default_opener_does_not_follow_a_redirect(self) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/elsewhere")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")

            def log_message(self, *args: object) -> None:
                return None

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            registry_http._OPENER.open(f"http://127.0.0.1:{server.server_address[1]}/start", timeout=5)
        self.assertEqual(caught.exception.code, 302)
        caught.exception.close()


class TextField(unittest.TestCase):
    def test_only_strings_are_kept_stripped_and_capped(self) -> None:
        self.assertEqual(text_field({"a": "  hi  "}, "a"), "hi")
        self.assertEqual(text_field({"a": "x" * 500}, "a", max_chars=10), "x" * 10)
        containers: tuple[object, ...] = ({"a": 1}, {"a": None}, {"a": ["x"]}, {}, [], None, "text")
        for container in containers:
            self.assertEqual(text_field(container, "a"), "")


if __name__ == "__main__":
    unittest.main()
