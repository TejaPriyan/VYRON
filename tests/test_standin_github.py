"""The live GitHub path, end to end over real sockets, against the stand-in server.

Nothing here reaches GitHub. The stand-in serves fixture data written from GitHub's documentation, so a pass
shows that VYRON's transport, retry, cache, validation and HTTP layers behave correctly when GitHub answers in
those shapes. It does not show that GitHub answers that way: ``scripts/live_github_check.py`` is that check.
"""

import _paths  # noqa: F401

import asyncio
import http.client
import json
import logging
import threading
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from github_fakes import commit_activity_payload
from standin_github import ORG, SCENARIOS, StandIn, start_standin
from vyron_api.adapters.memory_cache import InMemoryTTLCache
from vyron_api.api.bootstrap import build_seed_application
from vyron_api.api.hostcheck import LOOPBACK_HOSTS
from vyron_api.api.routes import OPERATIONS
from vyron_api.api.schemas import COMPONENTS
from vyron_api.api.validate import validate
from vyron_api.api.wsgi import make_dev_server
from vyron_api.github.normalize import MalformedGitHubPayloadError
from vyron_api.github.provider import LiveGitHubProvider, build_live_provider
from vyron_api.github.transport import UrllibGitHubTransport
from vyron_api.github.url import RepoRef
from vyron_api.providers.github import (
    CacheStatus,
    ContributorsStatus,
    GitHubError,
    GitHubForbiddenError,
    GitHubMovedError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)

T0 = 1_792_577_730.0


def ref(name: str) -> RepoRef:
    return RepoRef(ORG, name)


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


class StandInCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.standin: StandIn = start_standin()
        self.addCleanup(self._stop)
        self.clock = Clock()

    def _stop(self) -> None:
        self.standin.shutdown()
        self.standin.server_close()

    def provider(self, *, token: str | None = None) -> LiveGitHubProvider:
        transport = UrllibGitHubTransport(
            token=token, base_url=self.standin.base, allow_local_test_server=True, timeout=5
        )
        return LiveGitHubProvider(transport, InMemoryTTLCache(), clock=self.clock)

    def paths(self) -> list[str]:
        return [entry["path"] for entry in self.standin.log_copy()]


class ProviderAgainstStandInTest(StandInCase):
    async def test_a_healthy_repository_is_read_with_two_requests_and_says_where_from(self) -> None:
        result = await self.provider().analyze_repository(ref("example-repo"))
        self.assertEqual(result.snapshot.full_name, "fixture-org/example-repo")
        self.assertEqual((result.snapshot.stars, result.snapshot.license_spdx), (1234, "MIT"))
        self.assertEqual(sum(result.languages.values()), 10_000)
        self.assertEqual(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(
            list(result.source_urls),
            [f"{self.standin.base}/repos/fixture-org/example-repo",
             f"{self.standin.base}/repos/fixture-org/example-repo/languages"],
        )  # fmt: skip
        self.assertEqual(self.paths(), ["/repos/fixture-org/example-repo", "/repos/fixture-org/example-repo/languages"])

    async def test_a_second_look_inside_a_minute_does_not_reach_github(self) -> None:
        provider = self.provider()
        first = await provider.analyze_repository(ref("example-repo"))
        self.clock.now += 30
        again = await provider.analyze_repository(ref("example-repo"))
        self.assertEqual(len(self.paths()), 2)
        self.assertEqual((again.cache_status, again.fetched_at), (CacheStatus.FRESH, first.fetched_at))

    async def test_after_a_minute_github_is_asked_with_the_validator_and_confirms_with_a_304(self) -> None:
        provider = self.provider()
        await provider.analyze_repository(ref("example-repo"))
        self.clock.now += 120
        again = await provider.analyze_repository(ref("example-repo"))
        self.assertEqual(again.cache_status, CacheStatus.REVALIDATED)
        revalidations = [e for e in self.standin.log_copy() if e["if_none_match"]]
        self.assertEqual(
            [e["if_none_match"] for e in revalidations],
            ['"fixture-example-repo"', '"fixture-example-repo-languages"'],
        )
        self.assertEqual(again.snapshot.stars, 1234)

    async def test_when_github_goes_away_the_older_copy_is_offered_and_labelled_stale(self) -> None:
        provider = self.provider()
        first = await provider.analyze_repository(ref("example-repo"))
        await asyncio.to_thread(self._stop)  # nothing is listening any more (off the loop: shutdown() blocks)
        self.clock.now += 600
        again = await provider.analyze_repository(ref("example-repo"))
        self.assertEqual(again.cache_status, CacheStatus.STALE)
        self.assertEqual(again.fetched_at, first.fetched_at)  # honest about its age
        self.assertEqual(again.snapshot.stars, 1234)

    async def test_with_no_copy_to_fall_back_on_an_unreachable_github_is_an_error(self) -> None:
        provider = self.provider()
        await asyncio.to_thread(self._stop)
        with self.assertRaises(GitHubUnavailableError):
            await provider.analyze_repository(ref("example-repo"))

    async def test_a_repository_with_nothing_optional_set_comes_back_empty_not_broken(self) -> None:
        result = await self.provider().analyze_repository(ref("quiet-repo"))
        snapshot = result.snapshot
        self.assertEqual((snapshot.archived, snapshot.license_spdx, snapshot.description), (True, None, None))
        self.assertEqual((list(snapshot.topics), snapshot.primary_language, snapshot.pushed_at), ([], None, None))
        self.assertEqual(dict(result.languages), {})

    async def test_a_licence_with_no_standard_identifier_keeps_githubs_name_for_it(self) -> None:
        result = await self.provider().analyze_repository(ref("other-license-repo"))
        self.assertEqual((result.snapshot.license_spdx, result.snapshot.license_name), (None, "Other"))

    async def test_names_may_be_written_in_any_case_and_come_back_as_github_spells_them(self) -> None:
        result = await self.provider().analyze_repository(RepoRef("Fixture-ORG", "Example-REPO"))
        self.assertEqual(result.snapshot.full_name, "fixture-org/example-repo")

    async def test_each_failure_the_ui_must_handle_arrives_as_its_own_error(self) -> None:
        cases: dict[str, type[Exception]] = {
            "missing-repo": GitHubNotFoundError,
            "private-repo": GitHubNotFoundError,
            "moved-repo": GitHubMovedError,
            "limited-repo": GitHubRateLimitedError,
            "gateway-repo": GitHubForbiddenError,
            "broken-repo": GitHubUnavailableError,
            "garbled-repo": GitHubError,
        }
        for name, expected in cases.items():
            with self.subTest(name):
                with self.assertRaises(expected) as caught:
                    await self.provider().analyze_repository(ref(name))
                self.assertIs(type(caught.exception), expected)

    async def test_a_rate_limit_minutes_away_is_reported_with_its_wait_not_slept_through(self) -> None:
        started = time.monotonic()
        with self.assertRaises(GitHubRateLimitedError) as caught:
            await self.provider().analyze_repository(ref("limited-repo"))
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertGreater(caught.exception.wait_seconds, 100)
        self.assertEqual(len(self.paths()), 1)

    async def test_one_transient_failure_is_retried_and_the_read_succeeds(self) -> None:
        result = await self.provider().analyze_repository(ref("flaky-repo"))
        self.assertEqual(result.snapshot.full_name, "fixture-org/flaky-repo")
        self.assertEqual(self.paths().count("/repos/fixture-org/flaky-repo"), 2)  # the 503 and the retry

    async def test_a_repository_that_is_private_is_never_analysed_even_with_a_token(self) -> None:
        with self.assertRaises(GitHubNotFoundError):
            await self.provider(token="ghp_NotARealToken0123456789abcdefghij").analyze_repository(ref("private-repo"))
        self.assertEqual(self.paths(), ["/repos/fixture-org/private-repo"])  # stopped before the second request

    async def test_the_token_is_sent_only_when_configured(self) -> None:
        await self.provider().analyze_repository(ref("example-repo"))
        self.assertEqual({e["authenticated"] for e in self.standin.log_copy()}, {False})
        self.standin.reset()
        await self.provider(token="ghp_NotARealToken0123456789abcdefghij").analyze_repository(ref("example-repo"))
        self.assertEqual({e["authenticated"] for e in self.standin.log_copy()}, {True})

    async def test_the_quota_follows_what_github_reported_on_its_last_response(self) -> None:
        provider = self.provider()
        await provider.analyze_repository(ref("example-repo"))
        state = await provider.rate_limit_status()
        self.assertEqual((state.limit, state.remaining, state.used), (60, 58, 2))
        self.assertNotIn("/rate_limit", self.paths())  # it was known already, so nobody was asked

    async def test_the_quota_is_asked_for_when_nothing_has_been_read_yet(self) -> None:
        state = await self.provider().rate_limit_status()
        self.assertEqual((state.limit, state.remaining), (60, 60))
        self.assertEqual(self.paths(), ["/rate_limit"])

    async def test_every_scenario_the_stand_in_offers_is_covered_by_a_test_here(self) -> None:
        exercised = {
            "example-repo", "quiet-repo", "private-repo", "moved-repo", "missing-repo", "limited-repo",
            "gateway-repo", "broken-repo", "flaky-repo", "garbled-repo",
            "huge-repo", "unreleased-repo", "burst-repo", "garbled-list-repo", "other-license-repo",
            "slow-stats-repo",
        }  # fmt: skip
        self.assertEqual(set(SCENARIOS), exercised)

    async def test_an_unknown_repository_and_odd_paths_are_404s(self) -> None:
        for name in ("nope-repo", "example-repo/issues"):
            with self.subTest(name), self.assertRaises(GitHubNotFoundError):
                await self.provider()._read(f"/repos/{ORG}/{name}")  # noqa: SLF001


class ActivityAgainstStandInTest(StandInCase):
    """The opt-in contributors and latest-release read, over real sockets. Fixture data, written from the docs."""

    def requests(self) -> list[tuple[str, str]]:
        return [(entry["path"], entry["query"]) for entry in self.standin.log_copy()]

    async def test_a_healthy_repository_costs_three_requests_and_the_list_asks_for_ten(self) -> None:
        result = await self.provider().repository_activity(ref("example-repo"))
        base = "/repos/fixture-org/example-repo"
        self.assertEqual(
            self.requests(),
            [(base, ""), (f"{base}/contributors", "per_page=10"), (f"{base}/releases/latest", "")],
        )
        self.assertIs(result.contributors_status, ContributorsStatus.LISTED)
        self.assertEqual([c.login for c in result.contributors][:2], ["fixture-person-a", "fixture-person-b"])
        assert result.latest_release is not None
        page = "https://github.com/fixture-org/example-repo/releases/tag/v1.2.3"
        self.assertEqual(result.latest_release.html_url, page)
        self.assertEqual(list(result.source_urls)[1], f"{self.standin.base}{base}/contributors?per_page=10")

    async def test_after_the_basic_analysis_it_adds_two_requests_not_three(self) -> None:
        provider = self.provider()
        await provider.analyze_repository(ref("example-repo"))
        self.clock.now += 20
        await provider.repository_activity(ref("example-repo"))
        self.assertEqual(len(self.paths()), 4)

    async def test_a_second_read_after_a_minute_sends_each_validator_and_is_revalidated(self) -> None:
        provider = self.provider()
        await provider.repository_activity(ref("example-repo"))
        self.clock.now += 120
        again = await provider.repository_activity(ref("example-repo"))
        self.assertIs(again.cache_status, CacheStatus.REVALIDATED)
        sent = [e["if_none_match"] for e in self.standin.log_copy() if e["if_none_match"]]
        self.assertEqual(
            sent,
            ['"fixture-example-repo"', '"fixture-example-repo-contributors"', '"fixture-example-repo-release"'],
        )
        self.assertEqual(len(again.contributors), 3)

    async def test_a_repository_with_nothing_to_list_and_no_release_says_so(self) -> None:
        result = await self.provider().repository_activity(ref("quiet-repo"))
        self.assertEqual((result.contributors_status, result.contributors, result.latest_release),
                         (ContributorsStatus.NONE, (), None))  # fmt: skip

    async def test_a_refused_list_is_declined_and_the_release_is_still_shown(self) -> None:
        result = await self.provider().repository_activity(ref("huge-repo"))
        self.assertIs(result.contributors_status, ContributorsStatus.DECLINED)
        self.assertIsNotNone(result.latest_release)
        self.assertEqual(len(self.paths()), 3)  # a plain 403 is not retried

    async def test_a_repository_without_a_release_still_shows_its_contributors(self) -> None:
        result = await self.provider().repository_activity(ref("unreleased-repo"))
        self.assertIsNone(result.latest_release)
        self.assertIs(result.contributors_status, ContributorsStatus.LISTED)

    async def test_a_rate_limit_on_the_list_is_an_error_with_a_wait_not_a_declined_list(self) -> None:
        with self.assertRaises(GitHubRateLimitedError) as caught:
            await self.provider().repository_activity(ref("burst-repo"))
        self.assertGreater(caught.exception.wait_seconds, 100)
        self.assertEqual(len(self.paths()), 2)  # the release was not asked for

    async def test_a_list_that_is_not_a_list_is_refused(self) -> None:
        with self.assertRaises(MalformedGitHubPayloadError):
            await self.provider().repository_activity(ref("garbled-list-repo"))

    async def test_a_private_repository_is_refused_before_its_contributors_are_asked(self) -> None:
        with self.assertRaises(GitHubNotFoundError):
            await self.provider().repository_activity(ref("private-repo"))
        self.assertEqual(self.paths(), ["/repos/fixture-org/private-repo"])

    async def test_repositories_that_fail_at_the_first_read_never_reach_the_second(self) -> None:
        for name in ("missing-repo", "moved-repo", "gateway-repo", "garbled-repo"):
            with self.subTest(name), self.assertRaises(GitHubError):
                await self.provider().repository_activity(ref(name))
        self.assertTrue(all(path.count("/") == 3 for path in self.paths()), self.paths())  # only /repos/org/name

    async def test_the_token_is_sent_on_all_three_requests_when_configured_and_never_logged(self) -> None:
        await self.provider(token="ghp_FIXTURE_TOKEN").repository_activity(ref("example-repo"))
        self.assertEqual([e["authenticated"] for e in self.standin.log_copy()], [True, True, True])
        self.assertNotIn("ghp_FIXTURE_TOKEN", json.dumps(self.standin.log_copy()))


@contextmanager
def api_over_standin(standin: StandIn, *, token: str | None = None) -> Iterator[int]:
    """The real HTTP API, on a free loopback port, with its GitHub provider pointed at the stand-in."""
    provider = build_live_provider(
        token=token, base_url=standin.base, allow_local_test_server=True, timeout=5
    )
    application = build_seed_application(github=provider, allowed_hosts=LOOPBACK_HOSTS, rate_limit=False)
    server = make_dev_server(application, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def fetch(port: int, path: str, headers: dict[str, str] | None = None) -> tuple[http.client.HTTPResponse, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        raw = response.read()
        return response, (json.loads(raw) if raw else None)
    finally:
        conn.close()


class ApiAgainstStandInTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Failures are expected here and the API logs each upstream one as a warning; keep that off the console.
        cls.quiet = logging.getLogger("vyron.api")
        cls.handler = logging.NullHandler()
        cls.quiet.addHandler(cls.handler)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.quiet.removeHandler(cls.handler)

    def setUp(self) -> None:
        self.standin = start_standin()
        self.addCleanup(self.standin.server_close)
        self.addCleanup(self.standin.shutdown)

    def paths(self) -> list[str]:
        return [entry["path"] for entry in self.standin.log_copy()]

    def test_the_analysis_route_returns_a_schema_valid_sourced_answer(self) -> None:
        with api_over_standin(self.standin) as port:
            response, body = fetch(port, "/api/v1/github/repositories/fixture-org/example-repo")
        self.assertEqual(response.status, 200)
        schema = COMPONENTS[OPERATIONS["get_repository_analysis"].response_schema]
        self.assertEqual(validate(body, schema, COMPONENTS), [])
        self.assertEqual((body["provenance"], body["cache_status"]), ("sourced", "fresh"))
        self.assertEqual(body["full_name"], "fixture-org/example-repo")
        self.assertTrue(all(url.startswith(self.standin.base) for url in body["source_urls"]))
        self.assertEqual(response.getheader("Cache-Control"), "public, max-age=60")

    def test_the_second_request_is_served_from_the_minute_cache(self) -> None:
        with api_over_standin(self.standin) as port:
            fetch(port, "/api/v1/github/repositories/fixture-org/example-repo")
            _, again = fetch(port, "/api/v1/github/repositories/fixture-org/example-repo")
        self.assertEqual(len(self.standin.log_copy()), 2)
        self.assertEqual(again["cache_status"], "fresh")

    def test_each_failure_becomes_the_documented_status_code_and_message(self) -> None:
        expected = {
            "missing-repo": (404, "not_found"),
            "private-repo": (404, "not_found"),
            "moved-repo": (404, "not_found"),
            "limited-repo": (503, "upstream_rate_limited"),
            "gateway-repo": (502, "upstream_error"),
            "broken-repo": (502, "upstream_error"),
            "garbled-repo": (502, "upstream_error"),
        }
        for name, (status, code) in expected.items():
            with self.subTest(name), api_over_standin(self.standin) as port:  # a fresh server: see the next test
                response, body = fetch(port, f"/api/v1/github/repositories/{ORG}/{name}")
                self.assertEqual((response.status, body["error"]["code"]), (status, code))
                self.assertEqual(validate(body, COMPONENTS["Error"], COMPONENTS), [])
                self.assertNotIn("linked", json.dumps(body))  # the gateway's own words are not repeated
                self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_the_activity_route_returns_a_schema_valid_sourced_answer_in_three_requests(self) -> None:
        with api_over_standin(self.standin) as port:
            response, body = fetch(port, "/api/v1/github/repositories/fixture-org/example-repo/activity")
        self.assertEqual(response.status, 200)
        schema = COMPONENTS[OPERATIONS["get_repository_activity"].response_schema]
        self.assertEqual(validate(body, schema, COMPONENTS), [])
        self.assertEqual((body["provenance"], body["contributors_status"]), ("sourced", "listed"))
        self.assertEqual(len(self.standin.log_copy()), 3)
        self.assertTrue(all(url.startswith(self.standin.base) for url in body["source_urls"]))
        self.assertEqual(response.getheader("Cache-Control"), "public, max-age=60")

    def test_the_basic_analysis_never_asks_for_contributors_or_releases(self) -> None:
        with api_over_standin(self.standin) as port:
            fetch(port, "/api/v1/github/repositories/fixture-org/example-repo")
        self.assertEqual(
            self.paths(), ["/repos/fixture-org/example-repo", "/repos/fixture-org/example-repo/languages"]
        )

    def test_each_edge_the_activity_page_must_handle_arrives_as_the_documented_answer(self) -> None:
        expected: dict[str, tuple[int, Any]] = {
            "quiet-repo": (200, ("none", None)),
            "huge-repo": (200, ("declined", "v1.2.3")),
            "unreleased-repo": (200, ("listed", None)),
            "burst-repo": (503, "upstream_rate_limited"),
            "garbled-list-repo": (502, "upstream_error"),
            "private-repo": (404, "not_found"),
            "missing-repo": (404, "not_found"),
            "gateway-repo": (502, "upstream_error"),
        }
        for name, (status, detail) in expected.items():
            with self.subTest(name), api_over_standin(self.standin) as port:
                response, body = fetch(port, f"/api/v1/github/repositories/{ORG}/{name}/activity")
                self.assertEqual(response.status, status, body)
                if status == 200:
                    release = body["latest_release"]
                    self.assertEqual((body["contributors_status"], release and release["tag_name"]), detail)
                else:
                    self.assertEqual(body["error"]["code"], detail)
                    self.assertNotIn("linked", json.dumps(body))

    def test_once_github_says_the_quota_is_spent_other_repositories_are_refused_without_asking(self) -> None:
        with api_over_standin(self.standin) as port:
            fetch(port, "/api/v1/github/repositories/fixture-org/limited-repo")
            asked = len(self.standin.log_copy())
            response, body = fetch(port, "/api/v1/github/repositories/fixture-org/example-repo")
        self.assertEqual((response.status, body["error"]["code"]), (503, "upstream_rate_limited"))
        self.assertGreater(int(response.getheader("Retry-After")), 100)
        self.assertEqual(len(self.standin.log_copy()), asked, "the server must not spend the last of the quota")

    def test_a_rate_limited_answer_tells_the_caller_how_long_to_wait(self) -> None:
        with api_over_standin(self.standin) as port:
            response, _ = fetch(port, "/api/v1/github/repositories/fixture-org/limited-repo")
        self.assertEqual(response.status, 503)
        self.assertGreater(int(response.getheader("Retry-After")), 100)

    def test_the_quota_route_reflects_the_requests_made(self) -> None:
        with api_over_standin(self.standin) as port:
            fetch(port, "/api/v1/github/repositories/fixture-org/example-repo")
            response, body = fetch(port, "/api/v1/github/rate-limit")
        self.assertEqual(response.status, 200)
        schema = COMPONENTS[OPERATIONS["get_github_rate_limit"].response_schema]
        self.assertEqual(validate(body, schema, COMPONENTS), [])
        self.assertEqual((body["limit"], body["remaining"], body["used"]), (60, 58, 2))

    def test_a_foreign_host_header_is_refused_before_github_is_asked(self) -> None:
        with api_over_standin(self.standin) as port:
            response, body = fetch(
                port, "/api/v1/github/repositories/fixture-org/example-repo", {"Host": f"rebind.example:{port}"}
            )
        self.assertEqual((response.status, body["error"]["code"]), (403, "forbidden"))
        self.assertEqual(self.standin.log_copy(), [])

    def test_concurrent_requests_for_one_repository_all_succeed(self) -> None:
        results: list[int] = []
        with api_over_standin(self.standin) as port:
            def go() -> None:
                results.append(fetch(port, "/api/v1/github/repositories/fixture-org/example-repo")[0].status)

            threads = [threading.Thread(target=go) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
        self.assertEqual(results, [200] * 6)


class StandInItselfTest(unittest.TestCase):
    def test_it_labels_everything_it_serves_as_fixture_data(self) -> None:
        standin = start_standin()
        self.addCleanup(standin.server_close)
        self.addCleanup(standin.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", standin.server_port, timeout=5)
        conn.request("GET", "/repos/fixture-org/example-repo")
        body = json.loads(conn.getresponse().read())
        conn.close()
        self.assertIn("not data from GitHub", body["description"])
        self.assertEqual(body["owner"]["login"], "fixture-org")

    def test_it_listens_on_loopback_only(self) -> None:
        standin = start_standin()
        self.addCleanup(standin.server_close)
        self.addCleanup(standin.shutdown)
        self.assertEqual(standin.server_address[0], "127.0.0.1")

    def test_the_request_log_never_holds_the_authorization_value(self) -> None:
        standin = start_standin()
        self.addCleanup(standin.server_close)
        self.addCleanup(standin.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", standin.server_port, timeout=5)
        conn.request("GET", "/repos/fixture-org/example-repo", headers={"Authorization": "Bearer ghp_SECRET_VALUE"})
        conn.getresponse().read()
        conn.request("GET", "/__standin/requests")
        raw = conn.getresponse().read().decode()
        conn.close()
        self.assertNotIn("ghp_SECRET_VALUE", raw)
        self.assertEqual(json.loads(raw)[0]["authenticated"], True)

    def test_commit_activity_is_202_twice_then_200_and_the_count_resets(self) -> None:
        """202 with ``{}`` while computing, then 200 with a weak ETag, as GitHub really did on 2026-11-04."""
        standin = start_standin()
        self.addCleanup(standin.server_close)
        self.addCleanup(standin.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", standin.server_port, timeout=5)
        self.addCleanup(conn.close)

        def ask(name: str) -> tuple[int, bytes]:
            conn.request("GET", f"/repos/fixture-org/{name}/stats/commit_activity")
            response = conn.getresponse()
            return response.status, response.read()

        answers = [ask("example-repo") for _ in range(4)]
        self.assertEqual([status for status, _ in answers], [202, 202, 200, 200])
        self.assertEqual(answers[0][1], b"{}")  # what GitHub really sent: an empty object, not an empty body
        self.assertEqual(json.loads(answers[2][1]), commit_activity_payload())
        self.assertEqual(len(json.loads(answers[2][1])), 52)
        standin.reset()
        self.assertEqual(ask("example-repo")[0], 202)

    def test_commit_activity_has_its_own_other_answers(self) -> None:
        standin = start_standin()
        self.addCleanup(standin.server_close)
        self.addCleanup(standin.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", standin.server_port, timeout=5)
        self.addCleanup(conn.close)
        seen = {}
        for name in ("slow-stats-repo", "slow-stats-repo", "slow-stats-repo", "quiet-repo", "missing-repo"):
            conn.request("GET", f"/repos/fixture-org/{name}/stats/commit_activity")
            response = conn.getresponse()
            response.read()
            seen.setdefault(name, []).append(response.status)
        self.assertEqual(seen, {"slow-stats-repo": [202, 202, 202], "quiet-repo": [204], "missing-repo": [404]})

    def test_a_ready_year_carries_a_weak_etag_and_a_matching_request_gets_304(self) -> None:
        standin = start_standin()
        self.addCleanup(standin.server_close)
        self.addCleanup(standin.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", standin.server_port, timeout=5)
        self.addCleanup(conn.close)
        path = "/repos/fixture-org/example-repo/stats/commit_activity"
        for _ in range(2):  # the two 202s
            conn.request("GET", path)
            conn.getresponse().read()
        conn.request("GET", path)
        ready = conn.getresponse()
        ready.read()
        etag = ready.getheader("ETag") or ""
        self.assertEqual((ready.status, etag.startswith('W/"'), ready.getheader("Cache-Control")),
                         (200, True, "public, max-age=60, s-maxage=60"))  # fmt: skip
        conn.request("GET", path, headers={"If-None-Match": etag})
        again = conn.getresponse()
        again.read()
        self.assertEqual(again.status, 304)

    def test_commit_activity_is_one_path_and_nothing_else_under_stats(self) -> None:
        standin = start_standin()
        self.addCleanup(standin.server_close)
        self.addCleanup(standin.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", standin.server_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", "/repos/fixture-org/example-repo/stats/participation")
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
