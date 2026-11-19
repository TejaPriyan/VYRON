"""Check VYRON's package route against the real PyPI and npm, and save what they sent.

    python3 scripts/capture_registry.py                      # read the default packages through VYRON's own route
    python3 scripts/capture_registry.py --capture captures   # also save each registry's raw answer as a file
    python3 scripts/capture_registry.py --standin URL        # the same against scripts/standin_registry.py
    python3 scripts/capture_registry.py --package pypi:requests --package npm:@types/node

WHY THIS EXISTS. The build sandbox VYRON was written in cannot reach PyPI or npm, so every other test of the package
route runs against ``scripts/standin_registry.py``, whose answers are FIXTURE data written from the registries'
documentation and from the shapes reported for flask and express. Those tests show VYRON handles those shapes; they
cannot show the registries send them. This script is the check against the real thing. The project owner ran it on
2026-11-15 and reported 10 of 10 passing (docs/REGISTRIES.md says what that does and does not show); a pass is
one run on one day.

For each package it (1) asks the registry directly and, with ``--capture``, saves the raw JSON it sent, so a fixture
can be built from a real answer; (2) asks VYRON's own application for the same package through
``GET /api/v1/registries/{registry}/package`` and checks the answer against the API's response schema; and (3) prints
what VYRON drew from it (version, how many dependencies, how many left out and why) so a person can compare that with
the package's page. A package VYRON cannot read is the most useful result here: it means a real answer has a shape
the fixtures do not, and the raw file it saved is the thing to build the next fixture from.

It then asks for the package's release history (``GET /api/v1/registries/{registry}/releases``), which VYRON builds from
the same document, so that read costs no request. The history is checked against the API's schema and against the raw
document by arithmetic that does not use VYRON's reader: every version in the document is either listed, omitted as
older, or counted as left out; and the oldest and newest listed dates equal what the raw document says for those
versions. PyPI documents the ``releases`` object as deprecated: if the real answer has none, the script says so and
fails, because then there is no timeline to draw for PyPI.

Nothing here needs a token, and none is read. Each package costs two requests (one direct, one through VYRON; the
release history reuses VYRON's copy), so the defaults cost about sixteen in all. It sends only the address of a
package to ``pypi.org`` and ``registry.npmjs.org``.

``--standin URL`` points both reads at a running ``scripts/standin_registry.py`` instead (use the address it prints) so
the script itself can be tried without a network; a pass there says nothing about PyPI or npm, and the output says so.

Exit status: 0 every package passed, 1 a check failed, 2 the script could not run (a registry unreachable, bad
arguments). A pass says VYRON handled those packages on that day; it does not say it will handle every package.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "apps" / "api" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from vyron_api.api.app import Request  # noqa: E402
from vyron_api.api.bootstrap import build_seed_application  # noqa: E402
from vyron_api.api.schemas import COMPONENTS  # noqa: E402
from vyron_api.api.validate import validate  # noqa: E402
from vyron_api.registries.http import USER_AGENT  # noqa: E402
from vyron_api.registries.npm import NPM_BASE_URL  # noqa: E402
from vyron_api.registries.provider import build_live_package_provider  # noqa: E402
from vyron_api.registries.pypi import PYPI_BASE_URL  # noqa: E402

DEFAULT_PACKAGES = (
    "pypi:flask",
    "pypi:requests",
    "pypi:numpy",
    "pypi:django",
    "npm:express",
    "npm:react",
    "npm:@types/node",
    "npm:left-pad",
)
#: What a registry says about a name that does not exist. VYRON must answer 404 for it, not an upstream error.
MISSING = ("pypi:vyron-check-package-that-does-not-exist", "npm:vyron-check-package-that-does-not-exist")


def direct_url(registry: str, name: str, pypi_base: str = PYPI_BASE_URL, npm_base: str = NPM_BASE_URL) -> str:
    if registry == "pypi":
        return f"{pypi_base}/pypi/{urllib.parse.quote(name, safe='')}/json"
    return f"{npm_base}/{urllib.parse.quote(name, safe='@').replace('/', '%2F')}"


def ask_registry(url: str) -> tuple[int, bytes]:
    """The registry's own answer, exactly as sent. A 404 is an answer, not an error."""
    # The address is built by direct_url() from the two fixed registry hosts, or from a --standin address.
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def split(spec: str) -> tuple[str, str]:
    registry, _, name = spec.partition(":")
    if registry not in ("pypi", "npm") or not name:
        raise SystemExit(f"{spec!r} is not registry:name, like pypi:flask or npm:@types/node")
    return registry, name


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def raw_versions(registry: str, raw: Any) -> dict[str, datetime | None] | None:
    """Each version in the registry's own document and the date it gives it, read here without VYRON's reader.

    PyPI: the earliest ``upload_time_iso_8601`` of the version's files. npm: ``time[version]``. ``None`` when the
    document has no such object (PyPI's ``releases`` is documented as deprecated).
    """
    if not isinstance(raw, dict):
        return None
    if registry == "pypi":
        releases = raw.get("releases")
        if not isinstance(releases, dict):
            return None
        found: dict[str, datetime | None] = {}
        for version, files in releases.items():
            moments = (
                [
                    _moment(f.get("upload_time_iso_8601")) or _moment(f.get("upload_time"))
                    for f in files
                    if isinstance(f, dict)
                ]
                if isinstance(files, list)
                else []
            )
            usable = [m for m in moments if m is not None]
            found[version] = min(usable) if usable else None
        return found
    times, versions = raw.get("time"), raw.get("versions")
    if not isinstance(times, dict) or not isinstance(versions, dict):
        return None
    return {version: _moment(times.get(version)) for version in versions}


def check_releases(application: Any, registry: str, name: str, raw_body: bytes) -> tuple[bool, list[str]]:
    """Ask VYRON for the release history and check it. Returns (passed, lines to print)."""
    query = urllib.parse.urlencode({"name": name})
    path = f"/api/v1/registries/{registry}/releases"
    response = asyncio.run(application.handle(Request("GET", path, query, {})))
    try:
        answer: Any = json.loads(response.body or b"{}")
        raw: Any = json.loads(raw_body)
    except ValueError:
        return False, ["       releases: an answer was not JSON"]
    expected = raw_versions(registry, raw)
    if response.status != 200:
        code = (answer.get("error") or {}).get("code") if isinstance(answer, dict) else None
        if expected is None:
            what = "`releases` object" if registry == "pypi" else "`time` object"
            return False, [
                f"       releases: FAIL the registry's document has no {what}: no timeline can be drawn ({code})"
            ]
        return False, [f"       releases: FAIL VYRON answered {response.status} ({code}) for a document that has dates"]
    lines: list[str] = []
    issues = validate(answer, COMPONENTS["PackageReleases"], COMPONENTS)
    lines += [f"       schema: {issue.path}: {issue.message}" for issue in issues]
    listed = answer["releases"]
    left_out = {item["kind"]: item["count"] for item in answer["left_out"]}
    problems: list[str] = []
    if expected is None:
        problems.append("VYRON listed releases for a document that has none")
    else:
        accounted = len(listed) + answer["omitted_older"] + sum(c for k, c in left_out.items() if k != "removed")
        if accounted != len(expected):
            problems.append(f"the document has {len(expected)} versions but VYRON accounts for {accounted}")
        by_version = {item["version"]: item["released_at"] for item in listed}
        for end in (listed[:1] if not answer["omitted_older"] else [], listed[-1:]):
            for item in end:
                theirs = expected.get(item["version"])
                if theirs is None or _moment(item["released_at"]) != theirs.replace(microsecond=0):
                    problems.append(
                        f"{item['version']} is dated {by_version[item['version']]} but the document says {theirs}"
                    )
    pre = sum(1 for item in listed if item["pre_release"])
    flagged = sum(1 for item in listed if item["status"] != "normal")
    span = f"{listed[0]['released_at'][:10]} to {listed[-1]['released_at'][:10]}" if listed else "no dates"
    omitted = f", {answer['omitted_older']} older omitted" if answer["omitted_older"] else ""
    lefts = ", ".join(f"{count} {kind}" for kind, count in left_out.items()) or "none"
    lines.insert(
        0,
        f"       releases: {len(listed)} listed ({span}), {pre} pre-release by number, {flagged} yanked or "
        f"deprecated{omitted}, left out: {lefts}; dates are {answer['date_meaning']}",
    )
    lines += [f"       releases: FAIL {problem}" for problem in problems]
    return not issues and not problems, lines


def run(specs: list[str], capture: Path | None, standin: str | None = None) -> int:
    pypi_base, npm_base = (
        (f"{standin.rstrip('/')}/pypi-host", f"{standin.rstrip('/')}/npm-host")
        if standin
        else (
            PYPI_BASE_URL,
            NPM_BASE_URL,
        )
    )
    provider = build_live_package_provider(
        pypi_base_url=pypi_base, npm_base_url=npm_base, allow_local_test_server=standin is not None
    )
    application = build_seed_application(packages=provider, rate_limit=False)
    failures = 0
    for spec in specs:
        registry, name = split(spec)
        label = f"{registry}:{name}"
        status, body = ask_registry(direct_url(registry, name, pypi_base, npm_base))
        if capture is not None:
            capture.mkdir(parents=True, exist_ok=True)
            safe = name.replace("/", "__").replace("@", "")
            (capture / f"{registry}-{safe}.{status}.json").write_bytes(body)
        query = urllib.parse.urlencode({"name": name})
        path = f"/api/v1/registries/{registry}/package"
        response = asyncio.run(application.handle(Request("GET", path, query, {})))
        answer: Any = json.loads(response.body or b"{}")
        wanted_missing = spec in MISSING
        if wanted_missing:
            ok = status == 404 and response.status == 404
            print(f"{'ok  ' if ok else 'FAIL'} {label}: registry said {status}, VYRON answered {response.status}")
            failures += 0 if ok else 1
            continue
        if response.status != 200:
            code = (answer.get("error") or {}).get("code") if isinstance(answer, dict) else None
            print(f"FAIL {label}: registry said {status}, VYRON answered {response.status} ({code})")
            failures += 1
            continue
        issues = validate(answer, COMPONENTS["PackageReport"], COMPONENTS)
        left_out = ", ".join(f"{item['count']} {item['kind']}" for item in answer["left_out"]) or "none"
        unreadable = sum(1 for item in answer["dependencies"] if not item["readable"])
        print(
            f"{'FAIL' if issues else 'ok  '} {label}: version {answer['version']}, "
            f"{len(answer['dependencies'])} dependencies ({unreadable} not readable by name), left out: {left_out}"
        )
        for issue in issues:
            print(f"       schema: {issue.path}: {issue.message}")
        failures += 1 if issues else 0
        if status == 200:
            passed, lines = check_releases(application, registry, name, body)
            for line in lines:
                print(line)
            failures += 0 if passed else 1
    print()
    if standin:
        print("These answers came from a local stand-in of FIXTURE data. A pass here says NOTHING ABOUT PYPI OR NPM.")
    else:
        print("These answers came from the real registries. Compare the counts with each package's page there.")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--package", action="append", help="registry:name to read (repeatable); default: a spread")
    parser.add_argument("--capture", type=Path, help="save each registry's raw answer into this folder")
    parser.add_argument("--standin", help="read from a running scripts/standin_registry.py at this address instead")
    args = parser.parse_args(argv)
    specs = list(args.package or DEFAULT_PACKAGES) + ([] if args.package else list(MISSING))
    try:
        return run(specs, args.capture, args.standin)
    except (urllib.error.URLError, OSError) as error:
        print(f"Could not reach a registry: {error}. Nothing was checked.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
