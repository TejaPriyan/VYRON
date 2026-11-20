import contextlib
import http.client
import io
import json
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlencode

from github_fakes import FakeGitHubProvider
from registry_provider_fakes import FakePackageProvider
from test_api_app import SAMPLES, get
from vyron_api.api import limits
from vyron_api.api.app import SECURITY_HEADERS
from vyron_api.api.bootstrap import build_seed_application
from vyron_api.api.clientip import select_client_ip
from vyron_api.api.hostcheck import LOOPBACK_HOSTS
from vyron_api.api.routes import OPERATIONS
from vyron_api.api.schemas import COMPONENTS
from vyron_api.api.validate import validate
from vyron_api.api.wsgi import make_dev_server
from vyron_api.devserver import StartupError, configure
from vyron_api.devserver import main as devserver_main


@contextmanager
def running(**kwargs: Any) -> Iterator[int]:
    """A real server on a free port over the seed app, with a fixture GitHub provider unless told otherwise."""
    app_kwargs: dict[str, Any] = {k: kwargs.pop(k) for k in ("rate_limit", "allowed_hosts", "github") if k in kwargs}
    app_kwargs.setdefault("github", FakeGitHubProvider())
    app_kwargs.setdefault("packages", FakePackageProvider())
    server = make_dev_server(build_seed_application(**app_kwargs), "127.0.0.1", 0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(
    port: int, method: str, path: str, *, headers: dict[str, str] | None = None, body: bytes | None = None
) -> tuple[http.client.HTTPResponse, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response, response.read()
    finally:
        conn.close()


class OverRealSocketsTest(unittest.TestCase):
    def test_meta_over_http(self) -> None:
        with running() as port:
            response, body = request(port, "GET", "/api/v1/meta")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json; charset=utf-8")
        content_length = response.getheader("Content-Length")
        assert content_length is not None
        self.assertEqual(int(content_length), len(body))
        data = json.loads(body)
        self.assertEqual(validate(data, COMPONENTS["Meta"], COMPONENTS), [])
        self.assertEqual(data["data_mode"], "seed")
        for name, value in SECURITY_HEADERS.items():
            self.assertEqual(response.getheader(name), value)

    def test_search_with_query_string_and_percent_encoding(self) -> None:
        with running() as port:
            response, body = request(port, "GET", "/api/v1/search?q=c%2B%2B&kind=language")
            self.assertEqual(response.status, 200)
            response, body = request(port, "GET", "/api/v1/search?q=next.js")
        self.assertEqual(json.loads(body)["items"][0]["slug"], "next-js")

    def test_conditional_get_over_http(self) -> None:
        with running() as port:
            first, _ = request(port, "GET", "/api/v1/entities?limit=3")
            etag = first.getheader("ETag")
            assert etag is not None
            again, body = request(port, "GET", "/api/v1/entities?limit=3", headers={"If-None-Match": etag})
        self.assertEqual((again.status, body), (304, b""))

    def test_errors_over_http(self) -> None:
        with running() as port:
            missing, body = request(port, "GET", "/api/v1/nope")
            self.assertEqual(missing.status, 404)
            self.assertEqual(json.loads(body)["error"]["code"], "not_found")
            wrong, _ = request(port, "POST", "/api/v1/meta", body=b"{}")
            self.assertEqual((wrong.status, wrong.getheader("Allow")), (405, "GET"))
            planned, body = request(port, "POST", "/api/v1/research", body=b"{}")
            self.assertEqual((planned.status, json.loads(body)["error"]["code"]), (501, "not_implemented"))
            invalid, body = request(port, "GET", "/api/v1/entities?limit=999")
            self.assertEqual(invalid.status, 422)
            self.assertEqual(json.loads(body)["error"]["details"][0]["field"], "limit")

    def test_oversized_body_is_rejected_without_reading_it_all(self) -> None:
        with running() as port:
            response, body = request(
                port, "POST", "/api/v1/research", body=b"x" * (limits.MAX_BODY_BYTES * 4),
                headers={"Content-Type": "application/json"},
            )  # fmt: skip
        self.assertEqual(response.status, 413)
        self.assertEqual(json.loads(body)["error"]["code"], "payload_too_large")

    def test_request_id_round_trip(self) -> None:
        with running() as port:
            response, body = request(port, "GET", "/api/v1/nope", headers={"X-Request-ID": "client-trace-0001"})
        self.assertEqual(response.getheader("X-Request-ID"), "client-trace-0001")
        self.assertEqual(json.loads(body)["error"]["request_id"], "client-trace-0001")

    def test_every_implemented_operation_works_over_http_and_matches_its_schema(self) -> None:
        with running(rate_limit=False) as port:
            for operation_id, (path, query) in SAMPLES.items():
                with self.subTest(operation=operation_id):
                    response, body = request(port, "GET", f"{path}?{urlencode(query, doseq=True)}")
                    self.assertEqual(response.status, 200, body)
                    schema_name = OPERATIONS[operation_id].response_schema
                    assert schema_name is not None
                    schema = COMPONENTS[schema_name]
                    self.assertEqual(validate(json.loads(body), schema, COMPONENTS), [])

    def test_rate_limit_over_http(self) -> None:
        with running() as port:
            statuses = [request(port, "GET", "/api/v1/search?q=a")[0].status for _ in range(61)]
        self.assertEqual(statuses.count(200), 60)
        self.assertEqual(statuses[-1], 429)

    def test_forwarded_for_cannot_evade_limits_by_default(self) -> None:
        with running() as port:
            statuses = [
                request(port, "GET", "/api/v1/search?q=a", headers={"X-Forwarded-For": f"9.9.9.{i}"})[0].status
                for i in range(61)
            ]
        self.assertEqual(statuses[-1], 429)

    def test_forwarded_for_is_honoured_behind_a_trusted_proxy(self) -> None:
        with running(trusted_proxy_hops=1) as port:
            statuses = [
                request(port, "GET", "/api/v1/search?q=a", headers={"X-Forwarded-For": f"9.9.9.{i}"})[0].status
                for i in range(61)
            ]
        self.assertEqual(set(statuses), {200})


class HostGuardOverRealSocketsTest(unittest.TestCase):
    """http.client sets Host from the address it connects to, so a rebinding page's name has to be sent by hand."""

    def test_a_loopback_only_server_answers_its_own_address_and_refuses_a_foreign_host(self) -> None:
        with running(allowed_hosts=LOOPBACK_HOSTS) as port:
            own, _ = request(port, "GET", "/api/v1/meta")  # Host: 127.0.0.1:<port>
            named, _ = request(port, "GET", "/api/v1/meta", headers={"Host": f"localhost:{port}"})
            foreign, body = request(port, "GET", "/api/v1/meta", headers={"Host": f"rebind.example:{port}"})
        self.assertEqual((own.status, named.status, foreign.status), (200, 200, 403))
        self.assertEqual(json.loads(body)["error"]["code"], "forbidden")

    def test_the_github_route_is_not_reachable_under_a_foreign_host(self) -> None:
        provider = FakeGitHubProvider()
        with running(allowed_hosts=LOOPBACK_HOSTS, github=provider) as port:
            path = "/api/v1/github/repositories/fixture-org/example-repo"
            refused, _ = request(port, "GET", path, headers={"Host": "rebind.example"})
            served, body = request(port, "GET", path)
        self.assertEqual((refused.status, served.status), (403, 200))
        self.assertEqual(len(provider.requested), 1)  # only the legitimate request reached the provider
        self.assertEqual(json.loads(body)["provenance"], "sourced")


class ClientIpTest(unittest.TestCase):
    def test_selection(self) -> None:
        forwarded = {"x-forwarded-for": "6.6.6.6, 7.7.7.7, 10.0.0.5"}
        self.assertEqual(select_client_ip("10.0.0.1", forwarded, 0), "10.0.0.1")
        self.assertEqual(select_client_ip("10.0.0.1", forwarded, 1), "10.0.0.5")
        self.assertEqual(select_client_ip("10.0.0.1", forwarded, 2), "7.7.7.7")
        self.assertEqual(select_client_ip("10.0.0.1", forwarded, 4), "10.0.0.1")  # too few hops: distrust
        self.assertEqual(select_client_ip("10.0.0.1", {}, 1), "10.0.0.1")
        self.assertEqual(select_client_ip(None, {}, 0), "unknown")

    def test_client_supplied_entries_are_never_used(self) -> None:
        spoofed = {"x-forwarded-for": "1.2.3.4, 10.0.0.5"}  # the client claimed 1.2.3.4
        self.assertEqual(select_client_ip("10.0.0.1", spoofed, 1), "10.0.0.5")

    def test_overlong_values_are_truncated(self) -> None:
        self.assertEqual(len(select_client_ip("x", {"x-forwarded-for": "a" * 500}, 1)), 64)


class DevServerEntryPointTest(unittest.TestCase):
    def test_argument_errors_do_not_start_a_server(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            devserver_main(["--port", "not-a-number"])


TOKEN = "ghp_NotARealToken0123456789abcdefghij"


class DevServerConfigureTest(unittest.TestCase):
    """What ``configure`` builds. It never calls GitHub: a provider only makes requests when a route is used."""

    def test_by_default_github_is_live_and_unauthenticated_and_only_loopback_hosts_are_answered(self) -> None:
        app, line = configure("127.0.0.1", environ={})
        self.assertIn("live", line)
        self.assertIn("unauthenticated", line)
        self.assertEqual(get(app, "/api/v1/meta", host="127.0.0.1:8000")[0].status, 200)
        self.assertEqual(get(app, "/api/v1/meta", host="rebind.example")[0].status, 403)

    def test_a_token_is_used_but_never_shown(self) -> None:
        app, line = configure("localhost", environ={"GITHUB_TOKEN": f"  {TOKEN}  "})
        self.assertIn("authenticated with GITHUB_TOKEN", line)
        for shown in (line, repr(app)):
            self.assertNotIn(TOKEN, shown)

    def test_a_token_is_refused_on_a_non_loopback_address(self) -> None:
        for host in ("0.0.0.0", "192.168.1.20", "example.com"):
            with self.subTest(host=host), self.assertRaises(StartupError) as caught:
                configure(host, environ={"GITHUB_TOKEN": TOKEN})
            self.assertNotIn(TOKEN, str(caught.exception))
        configure("0.0.0.0", environ={})  # without a token a wider bind is the person's choice

    def test_the_refusal_can_be_overridden_explicitly_and_the_banner_warns(self) -> None:
        app, line = configure("0.0.0.0", allow_token_on_network=True, environ={"GITHUB_TOKEN": TOKEN})
        self.assertIn("WARNING", line)
        self.assertIn("not loopback", line)
        self.assertNotIn(TOKEN, line + repr(app))
        _, quiet = configure("127.0.0.1", allow_token_on_network=True, environ={"GITHUB_TOKEN": TOKEN})
        self.assertNotIn("WARNING", quiet)  # on loopback there is nothing to warn about

    def test_the_override_is_available_on_the_command_line(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            devserver_main(["--host", "0.0.0.0", "--port", "0"], environ={"GITHUB_TOKEN": TOKEN})
        self.assertIn("--allow-token-on-network", stderr.getvalue())

    def test_a_non_loopback_bind_answers_any_host(self) -> None:
        app, _ = configure("0.0.0.0", environ={})
        self.assertEqual(get(app, "/api/v1/meta", host="lan-name.local")[0].status, 200)

    def test_a_malformed_token_is_a_startup_error_that_does_not_echo_it(self) -> None:
        for bad in ("two words", f"{TOKEN}\nX-Injected: 1", "tökén", "x" * 300):
            with self.subTest(bad=bad[:12]), self.assertRaises(StartupError) as caught:
                configure("127.0.0.1", environ={"GITHUB_TOKEN": bad})
            self.assertNotIn(bad, str(caught.exception))

    def test_a_blank_token_means_no_token(self) -> None:
        _, line = configure("127.0.0.1", environ={"GITHUB_TOKEN": "   "})
        self.assertIn("unauthenticated", line)

    def test_no_github_switches_both_routes_off_and_ignores_the_token(self) -> None:
        app, line = configure("0.0.0.0", github=False, environ={"GITHUB_TOKEN": TOKEN})
        self.assertIn("off", line)
        for path in ("/api/v1/github/repositories/fixture-org/example-repo", "/api/v1/github/rate-limit"):
            self.assertEqual(get(app, path)[0].status, 503)

    def test_the_command_line_reports_a_startup_error_and_exits_without_serving(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            devserver_main(["--host", "0.0.0.0", "--port", "0"], environ={"GITHUB_TOKEN": TOKEN})
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("refusing to use GITHUB_TOKEN", stderr.getvalue())
        self.assertNotIn(TOKEN, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
