"""The release-history check inside scripts/capture_registry.py, tried on answers made wrong on purpose.

That check is what will tell a person, on a machine that can reach PyPI and npm, whether VYRON read the real release
dates correctly. Here it is shown to notice a wrong date, a missing version and a document with no dates, using
FIXTURE documents (written by hand, see ``registry_fakes.py``). Nothing here reaches a registry.
"""

import _paths  # noqa: F401

import json
import unittest
from types import SimpleNamespace
from typing import Any

from capture_registry import check_releases, raw_versions
from registry_fakes import (
    FakeTransport,
    fixture_server_history,
    fixture_web_releases,
    npm_payload,
    pypi_payload,
)
from vyron_api.api.bootstrap import build_seed_application
from vyron_api.registries.provider import build_live_package_provider

PYPI_URL = "https://pypi.org/pypi/fixture-web/json"
NPM_URL = "https://registry.npmjs.org/fixture-server"


def application_for(url: str, document: dict[str, object]) -> Any:
    transport = FakeTransport({url: json.dumps(document).encode()})
    provider = build_live_package_provider(transport=transport)  # type: ignore[arg-type]
    return build_seed_application(packages=provider, rate_limit=False)


class Tampered:
    """An application whose releases answer is changed by ``edit`` before the check sees it."""

    def __init__(self, inner: Any, edit: Any) -> None:
        self.inner, self.edit = inner, edit

    async def handle(self, request: Any) -> Any:
        response = await self.inner.handle(request)
        body = json.loads(response.body)
        self.edit(body)
        return SimpleNamespace(status=response.status, body=json.dumps(body).encode())


def pypi_document() -> dict[str, object]:
    return pypi_payload("fixture-web", "2.1.0", [], releases=fixture_web_releases())


def npm_document() -> dict[str, object]:
    history = fixture_server_history()
    return npm_payload("fixture-server", "4.0.0", {}, history=history, unpublished={"2.5.0": "2016-08-01T10:15:30Z"})


def check(app: Any, registry: str, name: str, document: dict[str, object]) -> tuple[bool, str]:
    passed, lines = check_releases(app, registry, name, json.dumps(document).encode())
    return passed, "\n".join(lines)


class WhatTheCheckAccepts(unittest.TestCase):
    def test_a_correct_pypi_history(self) -> None:
        document = pypi_document()
        passed, text = check(application_for(PYPI_URL, document), "pypi", "fixture-web", document)
        self.assertTrue(passed, text)
        self.assertIn("releases: 23 listed (2014-03-12 to 2025-04-17)", text)

    def test_a_correct_npm_history_where_one_version_was_unpublished(self) -> None:
        document = npm_document()
        passed, text = check(application_for(NPM_URL, document), "npm", "fixture-server", document)
        self.assertTrue(passed, text)
        self.assertIn("left out: 1 removed", text)

    def test_subsecond_pypi_and_upload_time_fallback_timestamps(self) -> None:
        releases = {
            "0.1.0": [{"upload_time_iso_8601": "2010-04-16T14:29:37.458396Z"}],
            "0.2.0": [{"upload_time": "2011-05-01T08:15:22"}],
        }
        document = pypi_payload("fixture-web", "0.2.0", [], releases=releases)
        passed, text = check(application_for(PYPI_URL, document), "pypi", "fixture-web", document)
        self.assertTrue(passed, text)
        self.assertIn("releases: 2 listed (2010-04-16 to 2011-05-01)", text)

    def test_subsecond_npm_timestamps(self) -> None:
        history = {
            "1.0.0": "2014-03-14T09:09:20.762Z",
            "1.1.0": "2018-04-09T01:10:45.796Z",
        }
        document = npm_payload("fixture-server", "1.1.0", {}, history=history)
        passed, text = check(application_for(NPM_URL, document), "npm", "fixture-server", document)
        self.assertTrue(passed, text)
        self.assertIn("releases: 2 listed (2014-03-14 to 2018-04-09)", text)


class WhatTheCheckRefuses(unittest.TestCase):
    def tampered(self, edit: Any) -> tuple[bool, str]:
        document = pypi_document()
        app = Tampered(application_for(PYPI_URL, document), edit)
        return check(app, "pypi", "fixture-web", document)

    def test_a_newest_date_one_day_out(self) -> None:
        def edit(body: dict[str, Any]) -> None:
            body["releases"][-1]["released_at"] = "2025-04-18T10:00:00Z"

        passed, text = self.tampered(edit)
        self.assertFalse(passed)
        self.assertIn("2.1.0 is dated 2025-04-18T10:00:00Z but the document says", text)

    def test_an_oldest_date_out(self) -> None:
        def edit(body: dict[str, Any]) -> None:
            body["releases"][0]["released_at"] = "2014-03-13T10:15:30Z"

        passed, text = self.tampered(edit)
        self.assertFalse(passed)
        self.assertIn("0.1.0 is dated", text)

    def test_a_version_that_went_missing(self) -> None:
        def edit(body: dict[str, Any]) -> None:
            del body["releases"][10]

        passed, text = self.tampered(edit)
        self.assertFalse(passed)
        self.assertIn("the document has 24 versions but VYRON accounts for 23", text)

    def test_a_left_out_count_that_hides_a_version(self) -> None:
        def edit(body: dict[str, Any]) -> None:
            body["left_out"] = []

        passed, text = self.tampered(edit)
        self.assertFalse(passed)
        self.assertIn("accounts for 23", text)

    def test_an_answer_that_breaks_the_schema(self) -> None:
        def edit(body: dict[str, Any]) -> None:
            body["releases"][0]["status"] = "great"

        passed, text = self.tampered(edit)
        self.assertFalse(passed)
        self.assertIn("schema:", text)

    def test_a_document_with_no_releases_object_has_no_timeline(self) -> None:
        document = pypi_payload("fixture-web", "2.1.0", [], omit_releases=True)
        passed, text = check(application_for(PYPI_URL, document), "pypi", "fixture-web", document)
        self.assertFalse(passed)
        self.assertIn("no `releases` object: no timeline can be drawn (upstream_error)", text)

    def test_an_npm_document_with_no_time_object_has_no_timeline(self) -> None:
        document = npm_payload("fixture-server", "4.0.0", {})
        passed, text = check(application_for(NPM_URL, document), "npm", "fixture-server", document)
        self.assertFalse(passed)
        self.assertIn("no `time` object", text)

    def test_vyron_failing_where_the_document_has_dates_is_a_failure(self) -> None:
        document = pypi_document()

        class Broken:
            async def handle(self, request: Any) -> Any:
                return SimpleNamespace(status=502, body=b'{"error": {"code": "upstream_error"}}')

        passed, text = check(Broken(), "pypi", "fixture-web", document)
        self.assertFalse(passed)
        self.assertIn("VYRON answered 502 (upstream_error) for a document that has dates", text)

    def test_listing_releases_for_a_document_that_has_none_is_a_failure(self) -> None:
        with_dates = pypi_document()
        without = pypi_payload("fixture-web", "2.1.0", [], omit_releases=True)
        passed, text = check(application_for(PYPI_URL, with_dates), "pypi", "fixture-web", without)
        self.assertFalse(passed)
        self.assertIn("VYRON listed releases for a document that has none", text)


class ReadingTheRawDocument(unittest.TestCase):
    def test_pypi_takes_the_earliest_file_time_and_npm_takes_the_time_entry(self) -> None:
        pypi = raw_versions("pypi", pypi_document()) or {}
        self.assertEqual(len(pypi), 24)
        self.assertIsNone(pypi["0.0.9"])
        npm = raw_versions("npm", npm_document()) or {}
        self.assertEqual(len(npm), 11)  # the unpublished 2.5.0 has a time entry but is not in ``versions``

    def test_a_document_without_the_object_is_none(self) -> None:
        self.assertIsNone(raw_versions("pypi", {"info": {}}))
        self.assertIsNone(raw_versions("npm", {"versions": {}}))
        self.assertIsNone(raw_versions("pypi", []))


if __name__ == "__main__":
    unittest.main()
