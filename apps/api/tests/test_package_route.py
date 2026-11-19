"""The package route over the application: shapes, errors, and that a registry's words never reach the reader."""

import asyncio
import unittest

from registry_provider_fakes import FakePackageProvider
from test_api_app import get, make_app
from vyron_api.api.app import Request
from vyron_api.api.errors import STATUS_FOR_CODE, ErrorCode
from vyron_api.api.routes import OPERATIONS, RateBucket
from vyron_api.providers.registry import PackageCacheStatus, PackageRegistry
from vyron_api.registries.errors import (
    InvalidPackageNameError,
    PackageNotFoundError,
    RegistryError,
    RegistryRateLimitedError,
)
from vyron_api.registries.releases import ReleaseDatesMissingError

PATH = "/api/v1/registries/pypi/package"


class Answering(unittest.TestCase):
    def test_the_report(self) -> None:
        provider = FakePackageProvider()
        response, body = get(make_app(packages=provider), PATH, {"name": "fixture-web"})
        self.assertEqual(response.status, 200)
        self.assertEqual(provider.requested, [(PackageRegistry.PYPI, "fixture-web")])
        self.assertEqual(
            {k: body[k] for k in ("registry", "name", "cache_status", "provenance", "left_out")},
            {
                "registry": "pypi",
                "name": "fixture-web",
                "cache_status": "fresh",
                "provenance": "sourced",
                "left_out": [{"kind": "conditional", "count": 2}],
            },
        )
        self.assertEqual(
            body["dependencies"],
            [
                {"name": "fixture-dep", "requirement": ">=1.0", "readable": True},
                {"name": "fixture-link", "requirement": None, "readable": False},
            ],
        )
        self.assertEqual(body["source_url"], "https://registry.invalid/fixture-web")

    def test_npm_and_a_scoped_name_through_the_query(self) -> None:
        provider = FakePackageProvider(status=PackageCacheStatus.STALE)
        path = "/api/v1/registries/npm/package"
        response, body = get(make_app(packages=provider), path, {"name": "@fixture-scope/types"})
        self.assertEqual((response.status, body["cache_status"]), (200, "stale"))
        self.assertEqual(provider.requested, [(PackageRegistry.NPM, "@fixture-scope/types")])

    def test_it_may_be_cached_for_a_minute_and_has_its_own_rate_bucket(self) -> None:
        response, _ = get(make_app(), PATH, {"name": "fixture-web"})
        self.assertIn("max-age=60", response.headers.get("Cache-Control", ""))
        self.assertIs(OPERATIONS["get_package_dependencies"].rate_bucket, RateBucket.REGISTRY)


class Refusing(unittest.TestCase):
    def test_an_unknown_registry_a_missing_name_and_an_overlong_name(self) -> None:
        app = make_app()
        for path, query in (
            ("/api/v1/registries/rubygems/package", {"name": "x"}),
            (PATH, {}),
            (PATH, {"name": ""}),
            (PATH, {"name": "x" * 215}),
        ):
            with self.subTest(path=path, query=query):
                response, body = get(app, path, query)
                self.assertEqual((response.status, body["error"]["code"]), (422, "validation_error"))

    def test_a_name_the_registry_could_not_have_issued_names_the_field(self) -> None:
        app = make_app(packages=FakePackageProvider(fail_with=InvalidPackageNameError("not a valid PyPI package name")))
        response, body = get(app, PATH, {"name": "../x"})
        self.assertEqual(response.status, 422)
        self.assertEqual(body["error"]["details"][0]["field"], "name")

    def test_no_such_package(self) -> None:
        app = make_app(packages=FakePackageProvider(fail_with=PackageNotFoundError("x")))
        response, body = get(app, PATH, {"name": "a"})
        self.assertEqual((response.status, body["error"]["code"]), (404, "not_found"))
        self.assertEqual(body["error"]["message"], "package not found")

    def test_a_registry_that_cannot_be_read_says_only_the_fixed_words(self) -> None:
        secret = "words the registry sent: token=abc123"
        app = make_app(packages=FakePackageProvider(fail_with=RegistryError("PyPI could not be reached")))
        response, body = get(app, PATH, {"name": "a"})
        self.assertEqual(response.status, STATUS_FOR_CODE[ErrorCode.UPSTREAM_ERROR])
        self.assertEqual(body["error"]["message"], "PyPI could not be reached")
        self.assertNotIn(secret, response.body.decode())

    def test_a_registry_limiting_requests_asks_the_reader_to_wait(self) -> None:
        app = make_app(packages=FakePackageProvider(fail_with=RegistryRateLimitedError("PyPI is limiting requests")))
        response, body = get(app, PATH, {"name": "a"})
        self.assertEqual(response.status, STATUS_FOR_CODE[ErrorCode.UPSTREAM_RATE_LIMITED])
        self.assertEqual(body["error"]["code"], "upstream_rate_limited")
        self.assertEqual(response.headers.get("Retry-After"), "60")

    def test_switched_off_is_503_and_nothing_is_requested(self) -> None:
        response, body = get(make_app(packages=None), PATH, {"name": "fixture-web"})
        self.assertEqual((response.status, body["error"]["code"]), (503, "service_unavailable"))

    def test_the_route_is_get_only(self) -> None:
        response = asyncio.run(make_app().handle(Request("POST", PATH, "name=x", {})))
        self.assertEqual(response.status, 405)


RELEASES = "/api/v1/registries/pypi/releases"


class ReleaseHistory(unittest.TestCase):
    def test_the_history(self) -> None:
        provider = FakePackageProvider()
        response, body = get(make_app(packages=provider), RELEASES, {"name": "fixture-web"})
        self.assertEqual(response.status, 200)
        self.assertEqual(provider.requested, [(PackageRegistry.PYPI, "fixture-web")])
        documented = [
            "registry", "name", "registry_url", "releases", "omitted_older", "left_out", "date_meaning",
            "fetched_at", "cache_status", "source_url", "provenance",
        ]  # fmt: skip
        self.assertEqual(sorted(body), sorted(documented))
        self.assertEqual(
            {k: body[k] for k in ("registry", "name", "date_meaning", "cache_status", "provenance", "omitted_older")},
            {
                "registry": "pypi",
                "name": "fixture-web",
                "date_meaning": "first_file_upload",
                "cache_status": "fresh",
                "provenance": "sourced",
                "omitted_older": 4,
            },
        )
        self.assertEqual(body["left_out"], [{"kind": "undated", "count": 1}])
        self.assertEqual(
            body["releases"],
            [
                {"version": "1.0.0", "released_at": "2020-03-01T09:30:00Z", "pre_release": False, "status": "normal"},
                {"version": "1.1.0rc1", "released_at": "2021-06-15T08:00:00Z", "pre_release": True, "status": "normal"},
                {"version": "1.1.0", "released_at": "2021-07-02T10:00:00Z", "pre_release": False, "status": "yanked"},
            ],
        )

    def test_npm_says_what_its_date_is_and_a_scoped_name_travels_in_the_query(self) -> None:
        provider = FakePackageProvider(status=PackageCacheStatus.STALE)
        path = "/api/v1/registries/npm/releases"
        response, body = get(make_app(packages=provider), path, {"name": "@fixture-scope/types"})
        self.assertEqual((response.status, body["cache_status"], body["date_meaning"]), (200, "stale", "publish_time"))
        self.assertEqual(provider.requested, [(PackageRegistry.NPM, "@fixture-scope/types")])

    def test_it_may_be_cached_for_a_minute_and_shares_the_registry_rate_bucket(self) -> None:
        response, _ = get(make_app(), RELEASES, {"name": "fixture-web"})
        self.assertIn("max-age=60", response.headers.get("Cache-Control", ""))
        self.assertIs(OPERATIONS["get_package_releases"].rate_bucket, RateBucket.REGISTRY)
        self.assertIs(OPERATIONS["get_package_dependencies"].rate_bucket, RateBucket.REGISTRY)

    def test_an_unknown_registry_a_missing_name_and_an_overlong_name_are_refused(self) -> None:
        app = make_app()
        for path, query in (
            ("/api/v1/registries/rubygems/releases", {"name": "x"}),
            (RELEASES, {}),
            (RELEASES, {"name": ""}),
            (RELEASES, {"name": "x" * 215}),
        ):
            with self.subTest(path=path, query=query):
                response, body = get(app, path, query)
                self.assertEqual((response.status, body["error"]["code"]), (422, "validation_error"))

    def test_a_name_the_registry_could_not_have_issued_names_the_field(self) -> None:
        app = make_app(packages=FakePackageProvider(fail_with=InvalidPackageNameError("not a valid PyPI package name")))
        response, body = get(app, RELEASES, {"name": "../x"})
        self.assertEqual((response.status, body["error"]["details"][0]["field"]), (422, "name"))

    def test_no_such_package(self) -> None:
        app = make_app(packages=FakePackageProvider(fail_with=PackageNotFoundError("x")))
        response, body = get(app, RELEASES, {"name": "fixture-web"})
        self.assertEqual((response.status, body["error"]["code"]), (404, "not_found"))

    def test_a_registry_with_no_dates_is_a_bad_gateway_in_fixed_words(self) -> None:
        error = ReleaseDatesMissingError("PyPI sent no release dates for this package")
        response, body = get(make_app(packages=FakePackageProvider(fail_with=error)), RELEASES, {"name": "a"})
        self.assertEqual(response.status, STATUS_FOR_CODE[ErrorCode.UPSTREAM_ERROR])
        self.assertEqual(body["error"]["code"], "upstream_error")
        self.assertEqual(body["error"]["message"], "PyPI sent no release dates for this package")

    def test_a_registry_that_cannot_be_read_says_only_the_fixed_words(self) -> None:
        secret = "words the registry sent: token=abc123"
        app = make_app(packages=FakePackageProvider(fail_with=RegistryError("PyPI could not be reached")))
        response, body = get(app, RELEASES, {"name": "a"})
        self.assertEqual((response.status, body["error"]["message"]), (502, "PyPI could not be reached"))
        self.assertNotIn(secret, response.body.decode())

    def test_a_registry_limiting_requests_asks_the_reader_to_wait(self) -> None:
        app = make_app(packages=FakePackageProvider(fail_with=RegistryRateLimitedError("PyPI is limiting requests")))
        response, body = get(app, RELEASES, {"name": "a"})
        self.assertEqual(response.status, STATUS_FOR_CODE[ErrorCode.UPSTREAM_RATE_LIMITED])
        self.assertEqual((body["error"]["code"], response.headers.get("Retry-After")), ("upstream_rate_limited", "60"))

    def test_switched_off_is_503_and_nothing_is_requested(self) -> None:
        response, body = get(make_app(packages=None), RELEASES, {"name": "fixture-web"})
        self.assertEqual((response.status, body["error"]["code"]), (503, "service_unavailable"))

    def test_the_route_is_get_only(self) -> None:
        response = asyncio.run(make_app().handle(Request("POST", RELEASES, "name=x", {})))
        self.assertEqual(response.status, 405)


if __name__ == "__main__":
    unittest.main()
