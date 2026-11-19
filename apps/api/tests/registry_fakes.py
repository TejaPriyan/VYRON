"""Test doubles for the package-registry readers.

EVERYTHING HERE AND IN THE REGISTRY TESTS IS A FIXTURE WRITTEN BY HAND FROM THE SHAPE OF A REGISTRY'S ANSWER. None
of it was captured from PyPI or npm, and no test contacts either. The only observations of the real registries are
live runs of ``scripts/ingest_package.py`` reported by the project owner.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, amount: int | None = None) -> bytes:
        return self._data if amount is None else self._data[:amount]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeTransport:
    """Answers by URL and records every request. A value is a body (200), an int (that HTTP status) or an error."""

    def __init__(self, responses: dict[str, bytes | int | Exception]) -> None:
        self.responses = responses
        self.requested_urls: list[str] = []
        self.requested_headers: list[dict[str, str]] = []

    def open(self, req: urllib.request.Request, timeout: float = 10.0) -> FakeResponse:
        url = req.full_url
        self.requested_urls.append(url)
        self.requested_headers.append({k.lower(): v for k, v in req.header_items()})
        outcome = self.responses.get(url, 404)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            words = io.BytesIO(b'{"message": "upstream words"}')
            raise urllib.error.HTTPError(url, outcome, f"HTTP {outcome}", {}, words)  # type: ignore[arg-type]
        return FakeResponse(outcome)


# ---- the fixture packages served by scripts/standin_registry.py -----------------------------------------------
# Made-up names (``fixture-*``) so a screenshot labels itself. The payload SHAPES are written from the registries'
# documentation and from the real answers the project owner reported for flask and express; the dependency lists
# are invented. Nothing here is what PyPI or npm sent.


def pypi_files(*uploaded: str, yanked: bool = False) -> list[dict[str, object]]:
    """The file entries of one PyPI release, one per upload time (ISO 8601 with a zone, as PyPI writes them)."""
    return [
        {
            "filename": f"fixture-{index}.whl",
            "upload_time": stamp.removesuffix("Z").split(".")[0],
            "upload_time_iso_8601": stamp,
            "yanked": yanked,
            "yanked_reason": None,
        }
        for index, stamp in enumerate(uploaded)
    ]


def pypi_payload(
    name: str,
    version: str,
    requires: list[str] | None,
    summary: str = "",
    *,
    releases: dict[str, list[dict[str, object]]] | None = None,
    omit_releases: bool = False,
) -> dict[str, object]:
    """The part of PyPI's ``/pypi/<name>/json`` document VYRON reads (``releases`` is dropped by ``omit_releases``)."""
    document: dict[str, object] = {
        "info": {
            "name": name,
            "version": version,
            "summary": summary or f"{name}: a made-up package for VYRON's stand-in registry",
            "requires_dist": requires,
            "license": "MIT",
            "home_page": "",
        },
    }
    if not omit_releases:
        document["releases"] = releases if releases is not None else {}
    return document


def npm_payload(
    name: str,
    version: str,
    dependencies: dict[str, str] | None = None,
    *,
    development: dict[str, str] | None = None,
    peer: dict[str, str] | None = None,
    history: dict[str, str] | None = None,
    deprecated: tuple[str, ...] = (),
    unpublished: dict[str, str] | None = None,
) -> dict[str, object]:
    """The part of npm's package document VYRON reads: ``dist-tags.latest``, that version's declarations and,
    when ``history`` (version -> ISO time) is given, the other versions and the registry's ``time`` object."""
    declared: dict[str, object] = {"name": name, "version": version, "license": "MIT"}
    if dependencies is not None:
        declared["dependencies"] = dependencies
    if development:
        declared["devDependencies"] = development
    if peer:
        declared["peerDependencies"] = peer
    versions: dict[str, object] = {version: declared}
    document: dict[str, object] = {
        "name": name,
        "description": f"{name}: a made-up package for VYRON's stand-in registry",
        "dist-tags": {"latest": version},
        "versions": versions,
        "license": "MIT",
    }
    if history is not None:
        for other in history:
            if other != version and other not in (unpublished or {}):
                versions[other] = {"name": name, "version": other}
        for old in deprecated:
            entry = versions.get(old)
            if isinstance(entry, dict):
                entry["deprecated"] = "made-up deprecation notice for the stand-in"
        stamps = {**history, **(unpublished or {})}
        ordered = sorted(stamps.values())
        document["time"] = {"created": ordered[0], "modified": ordered[-1], **stamps}
    return document


def _stamp(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}T10:15:30.000000Z"


def fixture_web_releases() -> dict[str, list[dict[str, object]]]:
    """23 dated versions of fixture-web across 2014-2025, one yanked, four pre-releases, plus one with no files."""
    table = [
        ("0.1.0", (2014, 3, 12)), ("0.1.1", (2014, 3, 30)), ("0.2.0", (2014, 9, 2)), ("0.3.0", (2015, 2, 18)),
        ("0.3.1", (2015, 2, 19)), ("0.4.0", (2015, 11, 5)), ("1.0.0rc1", (2016, 4, 21)), ("1.0.0", (2016, 5, 9)),
        ("1.0.1", (2016, 5, 10)), ("1.1.0", (2016, 12, 1)), ("1.2.0", (2017, 6, 23)), ("1.3.0", (2018, 1, 11)),
        ("1.3.1", (2018, 1, 11)), ("1.4.0", (2019, 8, 30)), ("1.4.1", (2019, 9, 2)), ("1.5.0", (2022, 2, 14)),
        ("1.5.1", (2022, 2, 15)), ("1.5.2", (2022, 3, 1)), ("2.0.0a1", (2023, 10, 3)), ("2.0.0b1", (2023, 11, 14)),
        ("2.0.0rc1", (2024, 1, 9)), ("2.0.0", (2024, 2, 6)), ("2.1.0", (2025, 4, 17)),
    ]  # fmt: skip
    releases = {version: pypi_files(_stamp(*day)) for version, day in table}
    releases["1.5.1"] = pypi_files(_stamp(2022, 2, 15), yanked=True)
    releases["0.0.9"] = []  # a version the index lists with no files: it has no date
    return releases


def fixture_server_history() -> dict[str, str]:
    """Versions of fixture-server (npm) with the registry's ``time`` for each, in a made-up order of events."""
    return {
        "1.0.0": _stamp(2012, 6, 1), "1.1.0": _stamp(2012, 9, 14), "1.2.0": _stamp(2013, 1, 7),
        "2.0.0": _stamp(2014, 5, 20), "2.1.0": _stamp(2015, 3, 3), "3.0.0-beta.1": _stamp(2017, 10, 2),
        "3.0.0": _stamp(2018, 2, 9), "3.1.0": _stamp(2019, 7, 30), "3.2.0": _stamp(2021, 4, 12),
        "4.0.0-rc.1": _stamp(2023, 12, 1), "4.0.0": _stamp(2024, 3, 8),
    }  # fmt: skip


def fixture_wide_releases() -> dict[str, list[dict[str, object]]]:
    """312 versions: 272 about every 19 days from 2010, then a burst of 40 inside one day of 2025 (marks overlap)."""
    from datetime import date, timedelta

    releases: dict[str, list[dict[str, object]]] = {}
    day = date(2010, 1, 4)
    for index in range(312):
        version = f"1.{index // 40}.{index % 40}"
        if index < 272:
            releases[version] = pypi_files(_stamp(day.year, day.month, day.day))
            day += timedelta(days=19)
        else:
            minute = (index - 272) * 3
            releases[version] = pypi_files(f"2025-06-30T{9 + minute // 60:02d}:{minute % 60:02d}:00.000000Z")
    return releases


#: ``name -> one line saying what it is for``. A name not listed here is answered 404, as a registry would.
PYPI_SCENARIOS: dict[str, str] = {
    "fixture-web": "a web framework: 3 listed dependencies, 1 under an extra, 1 under a marker, 1 direct URL; "
    "23 dated releases (4 pre-releases, 1 yanked) and one version without files",
    "fixture-router": "depends on fixture-utils",
    "fixture-templates": "depends on fixture-utils and fixture-markup",
    "fixture-utils": "no dependencies",
    "fixture-markup": "no dependencies",
    "fixture-cycle-a": "depends on fixture-cycle-b",
    "fixture-cycle-b": "depends on fixture-cycle-a (a cycle)",
    "fixture-wide": "40 dependencies, and 312 dated releases (40 of them inside one day)",
    "fixture-undated": "an answer without PyPI's (deprecated) release information",
    "fixture-fragile": "depends on fixture-broken and fixture-limited, which cannot be opened",
    "fixture-limited": "answers 429",
    "fixture-broken": "answers 500 every time",
    "fixture-garbled": "answers 200 with a body that is not JSON",
}
NPM_SCENARIOS: dict[str, str] = {
    "fixture-server": "a server: 3 dependencies, an alias and a git dependency, development and peer dependencies; "
    "11 dated releases, one deprecated, one unpublished",
    "fixture-body": "depends on fixture-bytes",
    "fixture-cookie": "no dependencies",
    "@fixture-scope/types": "a scoped package with no dependencies and 3 dated releases",
    "fixture-bytes": "no dependencies",
}


def pypi_fixture(name: str) -> dict[str, object] | None:
    wide = [f"fixture-wide-part-{index:02d}>=1" for index in range(40)]
    table: dict[str, dict[str, object]] = {
        "fixture-web": pypi_payload(
            "fixture-web",
            "2.1.0",
            [
                "fixture-router>=1.4",
                "fixture-templates (>=3.0,<4)",
                "fixture-utils",
                "fixture-async>=1 ; extra == 'async'",
                "fixture-compat>=1 ; python_version < '3.10'",
                "fixture-direct @ https://example.invalid/fixture-direct.zip",
            ],
            releases=fixture_web_releases(),
        ),
        "fixture-router": pypi_payload("fixture-router", "1.5.0", ["fixture-utils>=2"]),
        "fixture-templates": pypi_payload("fixture-templates", "3.2.0", ["fixture-utils", "fixture-markup>=2.1"]),
        "fixture-utils": pypi_payload("fixture-utils", "2.3.0", None),
        "fixture-markup": pypi_payload("fixture-markup", "2.1.1", []),
        "fixture-cycle-a": pypi_payload("fixture-cycle-a", "1.0.0", ["fixture-cycle-b"]),
        "fixture-cycle-b": pypi_payload("fixture-cycle-b", "1.0.0", ["fixture-cycle-a"]),
        "fixture-wide": pypi_payload("fixture-wide", "1.7.31", wide, releases=fixture_wide_releases()),
        "fixture-undated": pypi_payload("fixture-undated", "1.0.0", [], omit_releases=True),
        "fixture-fragile": pypi_payload("fixture-fragile", "1.0.0", ["fixture-broken>=1", "fixture-limited>=1"]),
    }
    return table.get(name)


def npm_fixture(name: str) -> dict[str, object] | None:
    table: dict[str, dict[str, object]] = {
        "fixture-server": npm_payload(
            "fixture-server",
            "4.0.0",
            {
                "fixture-body": "^1.2.0",
                "fixture-cookie": "~0.6.0",
                "@fixture-scope/types": "^2.0.0",
                "fixture-alias": "npm:fixture-body@^1",
                "fixture-gitdep": "github:fixture-org/fixture-gitdep",
            },
            development={"fixture-test": "^1.0.0"},
            peer={"fixture-peer": ">=2"},
            history=fixture_server_history(),
            deprecated=("2.0.0",),
            unpublished={"2.5.0": _stamp(2016, 8, 1)},
        ),
        "fixture-body": npm_payload("fixture-body", "1.2.3", {"fixture-bytes": "^3.0.0"}),
        "fixture-cookie": npm_payload("fixture-cookie", "0.6.1", {}),
        "@fixture-scope/types": npm_payload(
            "@fixture-scope/types",
            "2.0.1",
            {},
            history={"1.0.0": _stamp(2021, 2, 3), "2.0.0": _stamp(2022, 8, 9), "2.0.1": _stamp(2022, 8, 11)},
        ),
        "fixture-bytes": npm_payload("fixture-bytes", "3.0.0", {}),
    }
    return table.get(name)
