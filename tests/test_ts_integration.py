"""Run the TypeScript test suites with Node's built-in runner, including the cross-language check.

Needs Node >= 22.18 (type stripping without flags) and nothing else: no npm packages. Skipped, with
the reason, when Node is missing or too old. The integration suite starts the real Python dev server
on an ephemeral localhost port and runs the TypeScript client against it.

Run from the repository root:
    python3 -m unittest discover -s tests
"""

import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from _server import start_dev_server, stop_server

ROOT = Path(__file__).resolve().parents[1]
CLIENT_TESTS = ROOT / "packages" / "api-client" / "src"
PROTOTYPE_TESTS = ROOT / "apps" / "web" / "prototype" / "src"
NODE = shutil.which("node")


def node_version() -> tuple[int, int, int] | None:
    if not NODE:
        return None
    out = subprocess.run([NODE, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(g) for g in match.groups()) if match else None  # type: ignore[return-value]


VERSION = node_version()
NODE_OK = VERSION is not None and VERSION >= (22, 18, 0)
SKIP_REASON = f"Node >= 22.18 is required (found {VERSION})"


def passed(tap_output: str) -> int:
    match = re.search(r"# pass (\d+)", tap_output)
    return int(match.group(1)) if match else 0


def run_node_tests(files: list[Path], extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}
    env.update(extra_env or {})
    assert NODE is not None
    return subprocess.run(
        [NODE, "--test", "--test-reporter=tap", *map(str, files)],  # tap keeps the "# fail 0" summary on any Node
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip


@unittest.skipUnless(NODE_OK, SKIP_REASON)
class TypeScriptUnitTests(unittest.TestCase):
    def test_client_unit_suite_passes(self) -> None:
        result = run_node_tests([CLIENT_TESTS / "client.test.ts"])
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])
        self.assertRegex(result.stdout, r"# tests \d+")
        self.assertRegex(result.stdout, r"# fail 0")

    def test_every_package_unit_suite_passes(self) -> None:
        files = sorted(f for f in ROOT.glob("packages/*/src/*.test.ts") if f.name != "integration.test.ts")
        names = {f.name for f in files}
        self.assertTrue({"map-state.test.ts", "outline.test.ts", "lod.test.ts", "states.test.ts"} <= names, names)
        self.assertTrue({"atlas.test.ts", "layout.test.ts", "camera.test.ts", "hull.test.ts"} <= names, names)
        result = run_node_tests(files)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])
        self.assertRegex(result.stdout, r"# fail 0")
        self.assertGreaterEqual(passed(result.stdout), 150)

    def test_prototype_unit_suites_pass(self) -> None:
        files = sorted(f for f in PROTOTYPE_TESTS.glob("*.test.ts") if not f.name.endswith("integration.test.ts"))
        self.assertTrue({"data.test.ts", "url.test.ts", "format.test.ts"} <= {f.name for f in files}, files)
        result = run_node_tests(files)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])
        self.assertRegex(result.stdout, r"# fail 0")
        self.assertGreaterEqual(passed(result.stdout), 20)


@unittest.skipUnless(NODE_OK, SKIP_REASON)
class CrossLanguageIntegration(unittest.TestCase):
    """The TypeScript client against the Python API, over a real socket, on seed data.

    The server for the client suite is the API wired to the stand-in GitHub (fixture data, scripts/standin_github.py),
    so the live-repository routes can be exercised without reaching GitHub.
    """

    def test_typescript_client_against_the_python_dev_server(self) -> None:
        server = start_dev_server([sys.executable, str(ROOT / "scripts" / "standin_github.py"), "--mode", "api"])
        try:
            result = run_node_tests([CLIENT_TESTS / "integration.test.ts"], {"VYRON_API_URL": server.url})
            self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-1000:])
            self.assertNotRegex(result.stdout, r"# skipped [1-9]", "the integration suite must not be skipped")
            self.assertRegex(result.stdout, r"# pass (19|[2-9]\d)")
        finally:
            stop_server(server.process)

    def test_prototype_data_loading_against_the_python_dev_server(self) -> None:
        """The map's own loading code, on the real API: 60 entities, 68 relationships, 20 clusters, complete."""
        server = start_dev_server()
        try:
            result = run_node_tests([PROTOTYPE_TESTS / "data.integration.test.ts"], {"VYRON_API_URL": server.url})
            self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-1000:])
            self.assertNotRegex(result.stdout, r"# skipped [1-9]", "the integration suite must not be skipped")
            self.assertGreaterEqual(passed(result.stdout), 7)
        finally:
            stop_server(server.process)


if __name__ == "__main__":
    unittest.main()
