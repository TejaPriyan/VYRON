"""The live package provider: one request per package, a ten-minute copy, the older copy when the registry fails.

Every registry answer here is a fixture written by hand (see ``registry_fakes.py``); no test reaches PyPI or npm.
"""

import asyncio
import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from registry_fakes import (
    FakeTransport,
    fixture_server_history,
    fixture_web_releases,
    npm_payload,
    pypi_payload,
)
from vyron_api.providers.registry import PackageCacheStatus, PackageRegistry, PackageResult, ReleaseResult
from vyron_api.registries import provider as provider_module
from vyron_api.registries.errors import (
    InvalidPackageNameError,
    PackageNotFoundError,
    RegistryError,
    RegistryRateLimitedError,
)
from vyron_api.registries.provider import FRESH_SECONDS, LivePackageProvider, build_live_package_provider
from vyron_api.registries.releases import ReleaseDatesMissingError

PYPI_URL = "https://pypi.org/pypi/fixture-web/json"
NPM_URL = "https://registry.npmjs.org/fixture-server"
PYPI = PackageRegistry.PYPI
NPM = PackageRegistry.NPM


def body() -> bytes:
    return json.dumps(pypi_payload("fixture-web", "2.1.0", ["fixture-router>=1.4"])).encode()


class Clock:
    def __init__(self) -> None:
        self.seconds = 1000.0
        self.moment = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> None:
        self.seconds += seconds
        self.moment += timedelta(seconds=seconds)


def make(transport: FakeTransport, clock: Clock | None = None) -> LivePackageProvider:
    live = build_live_package_provider(transport=transport)  # type: ignore[arg-type]
    if clock is not None:
        live._clock = clock.monotonic
        live._now = clock.now
    return live


def read(live: LivePackageProvider, registry: PackageRegistry, name: str) -> PackageResult:
    return asyncio.run(live.read_package(registry, name))


def read_releases(live: LivePackageProvider, registry: PackageRegistry, name: str) -> ReleaseResult:
    return asyncio.run(live.read_releases(registry, name))


def dated_body(*, with_dates: bool = True) -> bytes:
    document = pypi_payload(
        "fixture-web",
        "2.1.0",
        ["fixture-router>=1.4"],
        releases=fixture_web_releases() if with_dates else None,
        omit_releases=not with_dates,
    )
    return json.dumps(document).encode()


def npm_dated_body() -> bytes:
    document = npm_payload(
        "fixture-server",
        "4.0.0",
        {"fixture-body": "^1"},
        history=fixture_server_history(),
        deprecated=("2.0.0",),
        unpublished={"2.5.0": "2016-08-01T10:15:30.000000Z"},
    )
    return json.dumps(document).encode()


class Reading(unittest.TestCase):
    def test_a_pypi_package_with_the_values_and_where_they_were_read_from(self) -> None:
        clock = Clock()
        result = read(make(FakeTransport({PYPI_URL: body()}), clock), PYPI, "Fixture_Web")
        snapshot = result.snapshot
        self.assertEqual((snapshot.registry, snapshot.name, snapshot.version), (PYPI, "fixture-web", "2.1.0"))
        self.assertEqual(snapshot.source_url, PYPI_URL)
        self.assertEqual(snapshot.registry_url, "https://pypi.org/project/fixture-web/")
        self.assertEqual([(d.name, d.requirement) for d in snapshot.dependencies], [("fixture-router", ">=1.4")])
        self.assertEqual((snapshot.fetched_at, result.cache_status), (clock.moment, PackageCacheStatus.FRESH))

    def test_an_npm_package_keeps_its_exact_name_and_its_page(self) -> None:
        data = json.dumps(npm_payload("fixture-server", "4.0.0", {"fixture-body": "^1"})).encode()
        snapshot = read(make(FakeTransport({NPM_URL: data})), NPM, "fixture-server").snapshot
        page = "https://www.npmjs.com/package/fixture-server"
        self.assertEqual((snapshot.name, snapshot.registry_url), ("fixture-server", page))

    def test_a_scoped_npm_package_page_keeps_its_slash(self) -> None:
        url = "https://registry.npmjs.org/@fixture-scope%2Ftypes"
        data = json.dumps(npm_payload("@fixture-scope/types", "2.0.1", {})).encode()
        snapshot = read(make(FakeTransport({url: data})), NPM, "@fixture-scope/types").snapshot
        self.assertEqual(snapshot.registry_url, "https://www.npmjs.com/package/@fixture-scope/types")

    def test_a_homepage_is_kept_only_if_it_is_a_plain_web_address(self) -> None:
        for home, kept in (("https://example.invalid/p", True), ("javascript:alert(1)", False),
                           ("https://user:pw@example.invalid/", False), ("x" * 400, False), ("", False)):  # fmt: skip
            with self.subTest(home=home[:30]):
                self.assertEqual(provider_module._homepage(home) is not None, kept)


class Caching(unittest.TestCase):
    def test_a_second_read_inside_the_window_makes_no_request(self) -> None:
        clock, transport = Clock(), FakeTransport({PYPI_URL: body()})
        live = make(transport, clock)
        first = read(live, PYPI, "fixture-web")
        clock.advance(FRESH_SECONDS - 1)
        second = read(live, PYPI, "FIXTURE_WEB")  # the same package under another spelling
        self.assertEqual(len(transport.requested_urls), 1)
        self.assertEqual(second.cache_status, PackageCacheStatus.FRESH)
        self.assertEqual(second.snapshot.fetched_at, first.snapshot.fetched_at)

    def test_after_the_window_the_registry_is_asked_again(self) -> None:
        clock, transport = Clock(), FakeTransport({PYPI_URL: body()})
        live = make(transport, clock)
        read(live, PYPI, "fixture-web")
        clock.advance(FRESH_SECONDS + 1)
        later = read(live, PYPI, "fixture-web")
        self.assertEqual(len(transport.requested_urls), 2)
        self.assertEqual(later.snapshot.fetched_at, clock.moment)

    def test_the_two_registries_do_not_share_a_name(self) -> None:
        npm_body = json.dumps(npm_payload("fixture-web", "1.0.0", {})).encode()
        transport = FakeTransport({PYPI_URL: body(), "https://registry.npmjs.org/fixture-web": npm_body})
        live = make(transport)
        self.assertEqual(read(live, PYPI, "fixture-web").snapshot.version, "2.1.0")
        self.assertEqual(read(live, NPM, "fixture-web").snapshot.version, "1.0.0")

    def test_only_the_most_recently_used_copies_are_kept(self) -> None:
        names = [f"fixture-{index}" for index in range(4)]
        transport = FakeTransport({f"https://pypi.org/pypi/{n}/json": body() for n in names})
        with mock.patch.object(provider_module, "MAX_ENTRIES", 3):
            live = make(transport)
            for name in names:
                read(live, PYPI, name)
            self.assertEqual(len(transport.requested_urls), 4)
            read(live, PYPI, "fixture-3")  # kept
            read(live, PYPI, "fixture-0")  # was the oldest, so it is asked for again
        self.assertEqual(len(transport.requested_urls), 5)


class Failing(unittest.TestCase):
    def broken(self, status: int) -> tuple[Clock, FakeTransport, LivePackageProvider]:
        clock, transport = Clock(), FakeTransport({PYPI_URL: body()})
        live = make(transport, clock)
        read(live, PYPI, "fixture-web")
        clock.advance(FRESH_SECONDS + 60)
        transport.responses[PYPI_URL] = status
        return clock, transport, live

    def test_an_older_copy_is_shown_as_stale_with_its_original_time(self) -> None:
        for status in (500, 502, 429):
            with self.subTest(status=status):
                clock, _, live = self.broken(status)
                result = read(live, PYPI, "fixture-web")
                self.assertEqual(result.cache_status, PackageCacheStatus.STALE)
                self.assertEqual(result.snapshot.fetched_at, clock.moment - timedelta(seconds=FRESH_SECONDS + 60))

    def test_without_a_copy_the_error_is_raised(self) -> None:
        for status, error in ((500, RegistryError), (429, RegistryRateLimitedError)):
            with self.subTest(status=status), self.assertRaises(error):
                read(make(FakeTransport({PYPI_URL: status})), PYPI, "fixture-web")

    def test_a_package_that_is_gone_is_not_served_from_the_copy(self) -> None:
        _, _, live = self.broken(404)
        with self.assertRaises(PackageNotFoundError):
            read(live, PYPI, "fixture-web")

    def test_a_garbled_answer_falls_back_like_any_other_failure(self) -> None:
        _, transport, live = self.broken(500)
        transport.responses[PYPI_URL] = b"not json"
        self.assertEqual(read(live, PYPI, "fixture-web").cache_status, PackageCacheStatus.STALE)

    def test_a_bad_name_is_refused_before_any_request(self) -> None:
        transport = FakeTransport({})
        live = make(transport)
        for registry, name in ((PYPI, "../etc/passwd"), (PYPI, ""), (NPM, "a b"), (NPM, "x" * 300)):
            with self.subTest(name=name[:20]), self.assertRaises(InvalidPackageNameError):
                read(live, registry, name)
        self.assertEqual(transport.requested_urls, [])


class Addresses(unittest.TestCase):
    def test_another_registry_address_is_refused_unless_it_is_a_local_test_server_and_said_to_be(self) -> None:
        for kwargs in (
            {"pypi_base_url": "https://evil.example"},
            {"npm_base_url": "http://127.0.0.1:9"},  # loopback, but not allowed
            {"pypi_base_url": "http://example.com", "allow_local_test_server": True},
            {"pypi_base_url": "http://user:pw@127.0.0.1:9", "allow_local_test_server": True},
            {"npm_base_url": "http://127.0.0.1:9/?x=1", "allow_local_test_server": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_live_package_provider(**kwargs)

    def test_a_local_test_server_is_read_over_http_and_has_no_page_for_people(self) -> None:
        base = "http://127.0.0.1:9/pypi-host"
        url = f"{base}/pypi/fixture-web/json"
        transport = FakeTransport({url: body()})
        live = build_live_package_provider(
            pypi_base_url=base,
            allow_local_test_server=True,
            transport=transport,  # type: ignore[arg-type]
        )
        snapshot = read(live, PYPI, "fixture-web").snapshot
        self.assertEqual((snapshot.source_url, snapshot.registry_url), (url, None))


class ReleaseHistories(unittest.TestCase):
    def test_a_pypi_history_with_where_and_when_it_was_read(self) -> None:
        clock = Clock()
        result = read_releases(make(FakeTransport({PYPI_URL: dated_body()}), clock), PYPI, "Fixture_Web")
        history = result.history
        self.assertEqual((history.registry, history.name), (PYPI, "fixture-web"))
        self.assertEqual(history.source_url, PYPI_URL)
        self.assertEqual(history.registry_url, "https://pypi.org/project/fixture-web/")
        self.assertEqual((history.date_meaning, history.omitted_older), ("first_file_upload", 0))
        self.assertEqual((history.fetched_at, result.cache_status), (clock.moment, PackageCacheStatus.FRESH))
        versions = [record.version for record in history.releases]
        self.assertEqual((versions[0], versions[-1], len(versions)), ("0.1.0", "2.1.0", 23))
        self.assertEqual(history.left_out, (("undated", 1),))
        self.assertEqual({r.version for r in history.releases if r.status == "yanked"}, {"1.5.1"})

    def test_an_npm_history_counts_what_was_unpublished_and_flags_what_is_deprecated(self) -> None:
        result = read_releases(make(FakeTransport({NPM_URL: npm_dated_body()})), NPM, "fixture-server")
        history = result.history
        self.assertEqual(history.date_meaning, "publish_time")
        self.assertEqual(history.left_out, (("removed", 1),))
        self.assertEqual({r.version for r in history.releases if r.status == "deprecated"}, {"2.0.0"})
        self.assertEqual(len(history.releases), 11)

    def test_one_request_serves_the_dependencies_and_the_dates(self) -> None:
        for first in ("package", "releases"):
            with self.subTest(first=first):
                transport = FakeTransport({PYPI_URL: dated_body()})
                live = make(transport)
                steps = {"package": read, "releases": read_releases}
                steps[first](live, PYPI, "fixture-web")
                steps["package" if first == "releases" else "releases"](live, PYPI, "fixture-web")
                self.assertEqual(len(transport.requested_urls), 1)

    def test_a_second_read_inside_the_window_makes_no_request_and_after_it_the_registry_is_asked_again(self) -> None:
        clock, transport = Clock(), FakeTransport({PYPI_URL: dated_body()})
        live = make(transport, clock)
        first = read_releases(live, PYPI, "fixture-web")
        clock.advance(FRESH_SECONDS - 1)
        second = read_releases(live, PYPI, "FIXTURE_WEB")
        self.assertEqual((len(transport.requested_urls), second.cache_status), (1, PackageCacheStatus.FRESH))
        self.assertEqual(second.history.fetched_at, first.history.fetched_at)
        clock.advance(2)
        later = read_releases(live, PYPI, "fixture-web")
        self.assertEqual(len(transport.requested_urls), 2)
        self.assertEqual(later.history.fetched_at, clock.moment)

    def test_the_two_registries_do_not_share_a_name(self) -> None:
        npm_url = "https://registry.npmjs.org/fixture-web"
        npm_body = json.dumps(npm_payload("fixture-web", "1.0.0", {}, history={"1.0.0": "2020-01-01T00:00:00Z"}))
        live = make(FakeTransport({PYPI_URL: dated_body(), npm_url: npm_body.encode()}))
        self.assertEqual(read_releases(live, PYPI, "fixture-web").history.date_meaning, "first_file_upload")
        self.assertEqual(read_releases(live, NPM, "fixture-web").history.date_meaning, "publish_time")

    def test_only_the_most_recently_used_histories_are_kept(self) -> None:
        names = [f"fixture-{index}" for index in range(4)]
        transport = FakeTransport({f"https://pypi.org/pypi/{n}/json": dated_body() for n in names})
        with mock.patch.object(provider_module, "MAX_HISTORIES", 3):
            live = make(transport)
            for name in names:
                read_releases(live, PYPI, name)
            self.assertEqual(len(transport.requested_urls), 4)
            read_releases(live, PYPI, "fixture-3")  # kept
            read_releases(live, PYPI, "fixture-0")  # was the oldest, so it is asked for again
        self.assertEqual(len(transport.requested_urls), 5)

    def test_a_local_test_server_has_no_page_for_people_and_says_so(self) -> None:
        local = "http://127.0.0.1:9/pypi/fixture-web/json"
        live = build_live_package_provider(
            pypi_base_url="http://127.0.0.1:9",
            allow_local_test_server=True,
            transport=FakeTransport({local: dated_body()}),  # type: ignore[arg-type]
        )
        self.assertIsNone(read_releases(live, PYPI, "fixture-web").history.registry_url)


class ReleaseHistoriesFailing(unittest.TestCase):
    def broken(self, status: int | bytes) -> tuple[Clock, FakeTransport, LivePackageProvider]:
        clock, transport = Clock(), FakeTransport({PYPI_URL: dated_body()})
        live = make(transport, clock)
        read_releases(live, PYPI, "fixture-web")
        clock.advance(FRESH_SECONDS + 60)
        transport.responses[PYPI_URL] = status
        return clock, transport, live

    def test_an_older_history_is_stale_with_its_original_time(self) -> None:
        for status in (500, 502, 429, b"not json"):
            with self.subTest(status=status):
                clock, _, live = self.broken(status)
                result = read_releases(live, PYPI, "fixture-web")
                self.assertEqual(result.cache_status, PackageCacheStatus.STALE)
                self.assertEqual(result.history.fetched_at, clock.moment - timedelta(seconds=FRESH_SECONDS + 60))
                self.assertEqual(len(result.history.releases), 23)

    def test_without_a_copy_the_error_is_raised(self) -> None:
        for status, error in ((500, RegistryError), (429, RegistryRateLimitedError)):
            with self.subTest(status=status), self.assertRaises(error):
                read_releases(make(FakeTransport({PYPI_URL: status})), PYPI, "fixture-web")

    def test_a_package_that_is_gone_is_not_served_from_the_copy(self) -> None:
        _, _, live = self.broken(404)
        with self.assertRaises(PackageNotFoundError):
            read_releases(live, PYPI, "fixture-web")

    def test_a_bad_name_is_refused_before_any_request(self) -> None:
        transport = FakeTransport({})
        live = make(transport)
        for registry, name in ((PYPI, "../etc/passwd"), (PYPI, ""), (NPM, "a b"), (NPM, "x" * 300)):
            with self.subTest(name=name[:20]), self.assertRaises(InvalidPackageNameError):
                read_releases(live, registry, name)
        self.assertEqual(transport.requested_urls, [])


class WhenTheRegistryHasNoDates(unittest.TestCase):
    def test_that_is_said_in_fixed_words_and_is_not_an_empty_history(self) -> None:
        live = make(FakeTransport({PYPI_URL: dated_body(with_dates=False)}))
        with self.assertRaises(ReleaseDatesMissingError) as caught:
            read_releases(live, PYPI, "fixture-web")
        self.assertEqual(str(caught.exception), "PyPI sent no release dates for this package")
        self.assertIsInstance(caught.exception, RegistryError)

    def test_npm_has_its_own_words(self) -> None:
        document = npm_payload("fixture-server", "4.0.0", {})  # no ``history``, so no ``time`` object
        live = make(FakeTransport({NPM_URL: json.dumps(document).encode()}))
        with self.assertRaises(ReleaseDatesMissingError) as caught:
            read_releases(live, NPM, "fixture-server")
        self.assertEqual(str(caught.exception), "npm sent no release dates for this package")

    def test_the_dependencies_in_that_same_answer_are_still_kept_and_not_asked_for_again(self) -> None:
        transport = FakeTransport({PYPI_URL: dated_body(with_dates=False)})
        live = make(transport)
        with self.assertRaises(ReleaseDatesMissingError):
            read_releases(live, PYPI, "fixture-web")
        snapshot = read(live, PYPI, "fixture-web")
        self.assertEqual((snapshot.cache_status, len(transport.requested_urls)), (PackageCacheStatus.FRESH, 1))
        self.assertEqual([d.name for d in snapshot.snapshot.dependencies], ["fixture-router"])

    def test_an_older_history_is_offered_as_stale_when_the_new_answer_has_none(self) -> None:
        clock, transport = Clock(), FakeTransport({PYPI_URL: dated_body()})
        live = make(transport, clock)
        first = read_releases(live, PYPI, "fixture-web")
        clock.advance(FRESH_SECONDS + 60)
        transport.responses[PYPI_URL] = dated_body(with_dates=False)
        again = read_releases(live, PYPI, "fixture-web")
        self.assertEqual(again.cache_status, PackageCacheStatus.STALE)
        self.assertEqual(again.history.fetched_at, first.history.fetched_at)


if __name__ == "__main__":
    unittest.main()
