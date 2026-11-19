"""The package route, end to end over real sockets, against the stand-in registry.

Nothing here reaches PyPI or npm. The stand-in serves FIXTURE data (every package is called ``fixture-*``) written from
the registries' documentation and from the shapes reported for flask and express, so a pass shows that VYRON's
transport, cache, validation and HTTP layers behave correctly when a registry answers in those shapes. It does not
show that a registry answers that way: ``scripts/capture_registry.py`` records what the real ones send.
"""

import _paths  # noqa: F401

import asyncio
import http.client
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from registry_fakes import NPM_SCENARIOS, PYPI_SCENARIOS
from standin_registry import NPM_PREFIX, PYPI_PREFIX, StandIn, start_standin
from vyron_api.api.bootstrap import build_seed_application
from vyron_api.api.hostcheck import LOOPBACK_HOSTS
from vyron_api.api.wsgi import make_dev_server
from vyron_api.providers.registry import PackageCacheStatus, PackageRegistry
from vyron_api.registries.errors import PackageNotFoundError, RegistryError, RegistryRateLimitedError
from vyron_api.registries.npm import fetch_npm_package
from vyron_api.registries.provider import FRESH_SECONDS, LivePackageProvider, build_live_package_provider
from vyron_api.registries.pypi import fetch_pypi_package
from vyron_api.registries.releases import ReleaseDatesMissingError


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

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

    def provider(self) -> LivePackageProvider:
        base = self.standin
        fetchers: dict[PackageRegistry, Callable[[str], object]] = {
            PackageRegistry.PYPI: lambda name: fetch_pypi_package(
                name, timeout=5, base_url=base.pypi_base, allow_local_test_server=True
            ),
            PackageRegistry.NPM: lambda name: fetch_npm_package(
                name, timeout=5, base_url=base.npm_base, allow_local_test_server=True
            ),
        }
        return LivePackageProvider(fetchers, local_test_server=True, clock=self.clock)  # type: ignore[arg-type]


class ProviderAgainstStandInTest(StandInCase):
    async def test_a_package_is_read_with_one_request_and_says_where_from(self) -> None:
        result = await self.provider().read_package(PackageRegistry.PYPI, "fixture-web")
        snapshot = result.snapshot
        self.assertEqual((snapshot.name, snapshot.version), ("fixture-web", "2.1.0"))
        self.assertEqual(result.cache_status, PackageCacheStatus.FRESH)
        self.assertEqual(
            [(d.name, d.requirement, d.readable) for d in snapshot.dependencies],
            [
                ("fixture-direct", None, False),
                ("fixture-router", ">=1.4", True),
                ("fixture-templates", ">=3.0,<4", True),
                ("fixture-utils", None, True),
            ],
        )
        self.assertEqual([(kind, count) for kind, count in snapshot.left_out], [("conditional", 2)])
        self.assertEqual(snapshot.source_url, f"{self.standin.pypi_base}/pypi/fixture-web/json")
        self.assertIsNone(snapshot.registry_url)  # a stand-in has no page for people, and the report says so
        self.assertEqual(self.standin.log_copy(), [f"{PYPI_PREFIX}/pypi/fixture-web/json"])

    async def test_a_second_look_inside_ten_minutes_does_not_reach_the_registry(self) -> None:
        provider = self.provider()
        first = await provider.read_package(PackageRegistry.PYPI, "fixture-router")
        self.clock.now += FRESH_SECONDS - 1
        # The same package, spelled differently.
        again = await provider.read_package(PackageRegistry.PYPI, "FIXTURE_Router")
        self.assertEqual(len(self.standin.log_copy()), 1)
        self.assertEqual(again.cache_status, PackageCacheStatus.FRESH)
        self.assertEqual(again.snapshot.fetched_at, first.snapshot.fetched_at)

    async def test_after_ten_minutes_the_registry_is_asked_again(self) -> None:
        provider = self.provider()
        await provider.read_package(PackageRegistry.PYPI, "fixture-router")
        self.clock.now += FRESH_SECONDS + 1
        await provider.read_package(PackageRegistry.PYPI, "fixture-router")
        self.assertEqual(len(self.standin.log_copy()), 2)

    async def test_when_the_registry_goes_away_the_older_copy_is_offered_and_labelled_stale(self) -> None:
        provider = self.provider()
        first = await provider.read_package(PackageRegistry.PYPI, "fixture-router")
        await asyncio.to_thread(self._stop)  # nothing is listening any more (off the loop: shutdown() blocks)
        self.clock.now += FRESH_SECONDS + 60
        again = await provider.read_package(PackageRegistry.PYPI, "fixture-router")
        self.assertEqual(again.cache_status, PackageCacheStatus.STALE)
        self.assertEqual(again.snapshot.fetched_at, first.snapshot.fetched_at)  # honest about its age

    async def test_with_no_copy_to_fall_back_on_an_unreachable_registry_is_an_error(self) -> None:
        provider = self.provider()
        await asyncio.to_thread(self._stop)
        with self.assertRaises(RegistryError):
            await provider.read_package(PackageRegistry.PYPI, "fixture-router")

    async def test_a_scoped_npm_name_is_asked_for_whole(self) -> None:
        result = await self.provider().read_package(PackageRegistry.NPM, "@fixture-scope/types")
        self.assertEqual(result.snapshot.name, "@fixture-scope/types")
        self.assertEqual(self.standin.log_copy(), [f"{NPM_PREFIX}/@fixture-scope/types"])

    async def test_npm_dependencies_keep_what_is_usable_and_count_what_is_left_out(self) -> None:
        snapshot = (await self.provider().read_package(PackageRegistry.NPM, "fixture-server")).snapshot
        readable = {d.name: d.readable for d in snapshot.dependencies}
        self.assertEqual(readable["fixture-body"], True)
        self.assertEqual(readable["fixture-alias"], False)
        self.assertEqual(readable["fixture-gitdep"], False)
        self.assertEqual({kind for kind, _ in snapshot.left_out}, {"development", "peer"})

    async def test_release_dates_come_from_the_same_single_request_as_the_dependencies(self) -> None:
        provider = self.provider()
        history = (await provider.read_releases(PackageRegistry.PYPI, "fixture-web")).history
        self.assertEqual(len(history.releases), 23)
        self.assertEqual((history.releases[0].version, history.releases[-1].version), ("0.1.0", "2.1.0"))
        self.assertEqual(history.left_out, (("undated", 1),))
        self.assertEqual(
            {r.version for r in history.releases if r.pre_release}, {"1.0.0rc1", "2.0.0a1", "2.0.0b1", "2.0.0rc1"}
        )
        self.assertEqual([r.version for r in history.releases if r.status == "yanked"], ["1.5.1"])
        self.assertIsNone(history.registry_url)
        await provider.read_package(PackageRegistry.PYPI, "fixture-web")
        self.assertEqual(self.standin.log_copy(), [f"{PYPI_PREFIX}/pypi/fixture-web/json"])

    async def test_npm_release_dates_flag_a_deprecated_version_and_count_an_unpublished_one(self) -> None:
        history = (await self.provider().read_releases(PackageRegistry.NPM, "fixture-server")).history
        self.assertEqual((history.date_meaning, len(history.releases)), ("publish_time", 11))
        self.assertEqual([r.version for r in history.releases if r.status == "deprecated"], ["2.0.0"])
        self.assertEqual(history.left_out, (("removed", 1),))
        self.assertEqual({r.version for r in history.releases if r.pre_release}, {"3.0.0-beta.1", "4.0.0-rc.1"})

    async def test_a_scoped_npm_package_has_a_history_too(self) -> None:
        history = (await self.provider().read_releases(PackageRegistry.NPM, "@fixture-scope/types")).history
        self.assertEqual([r.version for r in history.releases], ["1.0.0", "2.0.0", "2.0.1"])

    async def test_a_very_long_history_is_returned_whole_and_in_order(self) -> None:
        history = (await self.provider().read_releases(PackageRegistry.PYPI, "fixture-wide")).history
        self.assertEqual((len(history.releases), history.omitted_older), (312, 0))
        moments = [record.released_at for record in history.releases]
        self.assertEqual(moments, sorted(moments))

    async def test_a_pypi_answer_without_the_deprecated_releases_object_has_no_timeline(self) -> None:
        provider = self.provider()
        with self.assertRaises(ReleaseDatesMissingError):
            await provider.read_releases(PackageRegistry.PYPI, "fixture-undated")
        snapshot = (await provider.read_package(PackageRegistry.PYPI, "fixture-undated")).snapshot
        self.assertEqual(snapshot.dependencies, ())  # the dependencies in that document are still usable

    async def test_a_wide_package_comes_back_whole(self) -> None:
        snapshot = (await self.provider().read_package(PackageRegistry.PYPI, "fixture-wide")).snapshot
        self.assertEqual(len(snapshot.dependencies), 40)

    async def test_each_failure_the_page_must_handle_arrives_as_its_own_error(self) -> None:
        cases: dict[str, type[Exception]] = {
            "fixture-missing": PackageNotFoundError,
            "fixture-limited": RegistryRateLimitedError,
            "fixture-broken": RegistryError,
            "fixture-garbled": RegistryError,
        }
        for name, expected in cases.items():
            with self.subTest(name):
                with self.assertRaises(expected) as caught:
                    await self.provider().read_package(PackageRegistry.PYPI, name)
                self.assertIs(type(caught.exception), expected)

    async def test_a_missing_package_is_not_answered_from_an_older_copy(self) -> None:
        provider = self.provider()
        self.standin.reset()
        with self.assertRaises(PackageNotFoundError):
            await provider.read_package(PackageRegistry.PYPI, "fixture-missing")
        self.assertEqual(len(self.standin.log_copy()), 1)

    async def test_the_stand_in_refuses_a_real_address_and_any_non_loopback_one(self) -> None:
        for address in ("https://example.com", "http://example.com", "http://192.0.2.1:8000"):
            with self.subTest(address):
                with self.assertRaises(ValueError):
                    build_live_package_provider(pypi_base_url=address, allow_local_test_server=True)
        with self.assertRaises(ValueError):
            build_live_package_provider(pypi_base_url=self.standin.pypi_base)  # allowed only when asked for

    async def test_every_scenario_the_stand_in_offers_is_covered_by_a_test_here(self) -> None:
        exercised = {
            "fixture-web", "fixture-router", "fixture-wide", "fixture-limited", "fixture-broken", "fixture-garbled",
            "fixture-server", "@fixture-scope/types", "fixture-undated",
        }  # fmt: skip
        # The rest are the leaves of the graphs the browser run opens; the page's tests read them.
        leaves = {"fixture-templates", "fixture-utils", "fixture-markup", "fixture-cycle-a", "fixture-cycle-b"}
        leaves |= {"fixture-body", "fixture-cookie", "fixture-bytes", "fixture-fragile"}
        self.assertEqual(set(PYPI_SCENARIOS) | set(NPM_SCENARIOS), exercised | leaves)


class ApiOverSocketsTest(StandInCase):
    """The HTTP contract the page relies on, through the real WSGI server."""

    def setUp(self) -> None:
        super().setUp()
        provider = build_live_package_provider(
            pypi_base_url=self.standin.pypi_base, npm_base_url=self.standin.npm_base, allow_local_test_server=True
        )
        application = build_seed_application(packages=provider, allowed_hosts=LOOPBACK_HOSTS, rate_limit=False)
        self.server = make_dev_server(application, "127.0.0.1", 0)
        import threading

        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(self._stop_api)

    def _stop_api(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def get(self, registry: str, name: str) -> tuple[int, dict[str, object], dict[str, str]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            query = urllib.parse.urlencode({"name": name})
            connection.request("GET", f"/api/v1/registries/{registry}/package?{query}")
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            return response.status, json.loads(response.read() or b"{}"), headers
        finally:
            connection.close()

    async def ask(self, registry: str, name: str) -> tuple[int, dict[str, object], dict[str, str]]:
        return await asyncio.to_thread(self.get, registry, name)

    async def test_a_package_report_has_the_documented_fields_and_the_stand_in_marks(self) -> None:
        status, body, _ = await self.ask("pypi", "fixture-web")
        self.assertEqual(status, 200)
        documented = [
            "registry", "name", "version", "summary", "license", "homepage", "registry_url", "dependencies",
            "left_out", "fetched_at", "cache_status", "source_url", "provenance",
        ]  # fmt: skip
        self.assertEqual(sorted(body), sorted(documented))
        self.assertIsNone(body["registry_url"])
        self.assertEqual((body["provenance"], body["cache_status"]), ("sourced", "fresh"))
        self.assertIn("made-up package", str(body["summary"]))

    async def test_a_scoped_name_travels_in_the_query_and_comes_back_whole(self) -> None:
        status, body, _ = await self.ask("npm", "@fixture-scope/types")
        self.assertEqual((status, body["name"]), (200, "@fixture-scope/types"))

    async def test_failures_carry_the_codes_the_page_has_words_for(self) -> None:
        expected = {
            "fixture-missing": (404, "not_found"),
            "fixture-limited": (503, "upstream_rate_limited"),
            "fixture-broken": (502, "upstream_error"),
            "fixture-garbled": (502, "upstream_error"),
        }
        for name, (status, code) in expected.items():
            with self.subTest(name):
                got, body, _ = await self.ask("pypi", name)
                self.assertEqual((got, body["error"]["code"]), (status, code))  # type: ignore[index]

    async def test_the_registrys_own_words_never_reach_the_answer(self) -> None:
        _, body, _ = await self.ask("pypi", "fixture-broken")
        self.assertNotIn("fixture: broken", json.dumps(body))

    async def test_a_name_that_is_not_a_package_name_is_refused_before_any_request(self) -> None:
        self.standin.reset()
        status, body, _ = await self.ask("pypi", "../../etc/passwd")
        self.assertEqual((status, body["error"]["code"]), (422, "validation_error"))  # type: ignore[index]
        self.assertEqual(self.standin.log_copy(), [])

    async def test_an_unknown_registry_is_refused(self) -> None:
        status, body, _ = await self.ask("rubygems", "fixture-web")
        self.assertIn(status, (404, 422))
        self.assertIn("error", body)

    def get_releases(self, registry: str, name: str) -> tuple[int, dict[str, object], dict[str, str]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            query = urllib.parse.urlencode({"name": name})
            connection.request("GET", f"/api/v1/registries/{registry}/releases?{query}")
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            return response.status, json.loads(response.read() or b"{}"), headers
        finally:
            connection.close()

    async def test_the_release_history_has_the_documented_fields_and_the_stand_in_marks(self) -> None:
        status, body, headers = await asyncio.to_thread(self.get_releases, "pypi", "fixture-web")
        self.assertEqual(status, 200)
        documented = [
            "registry", "name", "registry_url", "releases", "omitted_older", "left_out", "date_meaning",
            "fetched_at", "cache_status", "source_url", "provenance",
        ]  # fmt: skip
        self.assertEqual(sorted(body), sorted(documented))
        self.assertIsNone(body["registry_url"])
        self.assertEqual((body["provenance"], body["date_meaning"]), ("sourced", "first_file_upload"))
        self.assertEqual(len(body["releases"]), 23)  # type: ignore[arg-type]
        self.assertIn("max-age=60", headers.get("cache-control", ""))

    async def test_a_history_and_a_package_read_cost_one_registry_request_between_them(self) -> None:
        self.standin.reset()
        await asyncio.to_thread(self.get_releases, "npm", "fixture-server")
        await self.ask("npm", "fixture-server")
        self.assertEqual(self.standin.log_copy(), [f"{NPM_PREFIX}/fixture-server"])

    async def test_a_registry_with_no_dates_answers_502_in_fixed_words(self) -> None:
        status, body, _ = await asyncio.to_thread(self.get_releases, "pypi", "fixture-undated")
        self.assertEqual((status, body["error"]["code"]), (502, "upstream_error"))  # type: ignore[index]
        self.assertEqual(body["error"]["message"], "PyPI sent no release dates for this package")  # type: ignore[index]

    async def test_the_release_failures_carry_the_codes_the_page_has_words_for(self) -> None:
        expected = {
            "fixture-missing": (404, "not_found"),
            "fixture-limited": (503, "upstream_rate_limited"),
            "fixture-broken": (502, "upstream_error"),
            "fixture-garbled": (502, "upstream_error"),
        }
        for name, (status, code) in expected.items():
            with self.subTest(name):
                got, body, _ = await asyncio.to_thread(self.get_releases, "pypi", name)
                self.assertEqual((got, body["error"]["code"]), (status, code))  # type: ignore[index]

    async def test_the_second_look_is_served_from_the_copy(self) -> None:
        await self.ask("pypi", "fixture-utils")
        await self.ask("pypi", "fixture-utils")
        self.assertEqual(self.standin.log_copy(), [f"{PYPI_PREFIX}/pypi/fixture-utils/json"])


class CaptureScriptTest(StandInCase):
    """scripts/capture_registry.py, tried against the stand-in so the script itself is known to work."""

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        script = Path(__file__).resolve().parents[1] / "scripts" / "capture_registry.py"
        base = f"http://127.0.0.1:{self.standin.server_port}"
        return subprocess.run(  # noqa: S603 (our own script, fixed arguments)
            [sys.executable, str(script), "--standin", base, *args],
            capture_output=True, text=True, timeout=60, check=False,
        )  # fmt: skip

    async def test_it_reads_through_vyrons_route_and_says_a_stand_in_proves_nothing_about_the_registries(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            done = await asyncio.to_thread(
                self.run_script,
                "--package", "pypi:fixture-web", "--package", "npm:@fixture-scope/types",
                "--capture", folder,
            )  # fmt: skip
            saved = await asyncio.to_thread(lambda: sorted(path.name for path in Path(folder).iterdir()))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ok   pypi:fixture-web: version 2.1.0, 4 dependencies (1 not readable by name)", done.stdout)
        self.assertIn("NOTHING ABOUT PYPI OR NPM", done.stdout)
        self.assertEqual(saved, ["npm-fixture-scope__types.200.json", "pypi-fixture-web.200.json"])

    async def test_it_checks_the_release_history_against_the_raw_document(self) -> None:
        done = await asyncio.to_thread(
            self.run_script, "--package", "pypi:fixture-web", "--package", "npm:fixture-server"
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("releases: 23 listed (2014-03-12 to 2025-04-17), 4 pre-release by number", done.stdout)
        self.assertIn("1 yanked or deprecated, left out: 1 undated; dates are first_file_upload", done.stdout)
        self.assertIn("releases: 11 listed", done.stdout)
        self.assertIn("left out: 1 removed; dates are publish_time", done.stdout)

    async def test_it_fails_when_pypi_sends_no_releases_object_because_there_is_no_timeline(self) -> None:
        done = await asyncio.to_thread(self.run_script, "--package", "pypi:fixture-undated")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("FAIL the registry's document has no `releases` object: no timeline can be drawn", done.stdout)

    async def test_it_fails_when_vyron_cannot_read_what_the_registry_sent(self) -> None:
        done = await asyncio.to_thread(self.run_script, "--package", "pypi:fixture-garbled")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("FAIL pypi:fixture-garbled", done.stdout)

    async def test_a_package_the_registry_does_not_have_is_expected_only_for_the_name_made_to_be_missing(self) -> None:
        missing = "pypi:vyron-check-package-that-does-not-exist"
        done = await asyncio.to_thread(self.run_script, "--package", missing)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("registry said 404, VYRON answered 404", done.stdout)
        other = await asyncio.to_thread(self.run_script, "--package", "pypi:fixture-missing")
        self.assertEqual(other.returncode, 1, other.stdout)  # a 404 for a package that should exist is a failure

    async def test_it_refuses_a_name_without_a_registry(self) -> None:
        done = await asyncio.to_thread(self.run_script, "--package", "flask")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("is not registry:name", done.stderr)


class StandInServerTest(StandInCase):
    def request(self, path: str) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.standin.server_port, timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    async def test_it_listens_on_loopback_only(self) -> None:
        self.assertEqual(self.standin.server_address[0], "127.0.0.1")

    async def test_it_logs_what_was_asked_and_can_forget_it(self) -> None:
        await asyncio.to_thread(self.request, f"{PYPI_PREFIX}/pypi/fixture-utils/json")
        _, body = await asyncio.to_thread(self.request, "/__standin/requests")
        self.assertEqual(json.loads(body), [f"{PYPI_PREFIX}/pypi/fixture-utils/json"])
        await asyncio.to_thread(self.request, "/__standin/reset")
        self.assertEqual(self.standin.log_copy(), [])

    async def test_every_package_it_serves_says_it_is_made_up(self) -> None:
        for name in set(PYPI_SCENARIOS) - {"fixture-limited", "fixture-broken", "fixture-garbled"}:
            status, body = await asyncio.to_thread(self.request, f"{PYPI_PREFIX}/pypi/{name}/json")
            with self.subTest(name):
                self.assertEqual(status, 200)
                self.assertIn("made-up", json.loads(body)["info"]["summary"])
        for name in NPM_SCENARIOS:
            status, body = await asyncio.to_thread(self.request, f"{NPM_PREFIX}/{name}")
            with self.subTest(name):
                self.assertEqual(status, 200)

    async def test_anything_else_is_not_found(self) -> None:
        status, _ = await asyncio.to_thread(self.request, "/pypi/flask/json")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
