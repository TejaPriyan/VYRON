"""Structure of the CI workflow, checked without running GitHub Actions.

The workflow has NEVER RUN, so nothing here shows that it works on GitHub; it only catches mistakes that
can be seen in the file (a missing script, a secret reference, a write permission, a Python version that
the code does not support). PyYAML is not a project dependency: the tests are skipped when it is absent.

Run from the repository root:  python3 -m unittest discover -s tests
"""

import _paths  # noqa: F401  (puts the tooling directories on sys.path)

import os
import re
import unittest
from pathlib import Path

from _paths import ROOT

try:
    import yaml
except ImportError:  # pragma: no cover - depends on the environment
    yaml = None

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def load() -> dict:
    assert yaml is not None
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1 reads the bare key `on` as the boolean True.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


@unittest.skipUnless(yaml, "PyYAML is not installed")
class WorkflowTest(unittest.TestCase):
    def test_declares_the_expected_jobs_and_triggers(self) -> None:
        data = load()
        self.assertEqual(set(data["jobs"]), {"stdlib", "api"})
        self.assertIn("pull_request", data["on"])
        self.assertEqual(data["on"]["push"]["branches"], ["main"])

    def test_token_permissions_are_read_only(self) -> None:
        self.assertEqual(load()["permissions"], {"contents": "read"})

    def test_no_secret_is_referenced(self) -> None:
        self.assertNotRegex(WORKFLOW.read_text(encoding="utf-8"), r"secrets\.")

    def test_the_stdlib_job_runs_the_strict_verification_script(self) -> None:
        steps = load()["jobs"]["stdlib"]["steps"]
        commands = [step.get("run", "") for step in steps]
        self.assertIn("scripts/verify_stdlib.sh --strict", commands)
        script = ROOT / "scripts" / "verify_stdlib.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(os.access(script, os.X_OK), "the script must be executable for the runner to start it")

    def test_python_versions_include_the_oldest_supported(self) -> None:
        job = load()["jobs"]["stdlib"]
        versions = job["strategy"]["matrix"]["python"]
        self.assertIn("3.12", versions)
        pyproject = (ROOT / "apps" / "api" / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.12"', pyproject)

    def test_node_is_new_enough_for_type_stripping(self) -> None:
        steps = load()["jobs"]["stdlib"]["steps"]
        node = next(step for step in steps if str(step.get("uses", "")).startswith("actions/setup-node"))
        major = int(re.match(r"\d+", str(node["with"]["node-version"])).group())  # type: ignore[union-attr]
        self.assertGreaterEqual(major, 22)

    def test_typescript_is_installed_with_an_exact_version(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(text, r"npm install --global typescript@\d+\.\d+\.\d+")

    def test_every_script_the_workflow_runs_exists(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for path in set(re.findall(r"\bscripts/[A-Za-z0-9_./-]+", text)):
            self.assertTrue((ROOT / path).exists(), path)

    def test_the_api_job_runs_the_full_toolchain_from_the_api_directory(self) -> None:
        job = load()["jobs"]["api"]
        self.assertEqual(job["defaults"]["run"]["working-directory"], "apps/api")
        commands = " ".join(step.get("run", "") for step in job["steps"])
        for tool in ("ruff check", "ruff format --check", "mypy", "pytest"):
            self.assertIn(tool, commands)

    def test_the_file_says_it_is_unverified(self) -> None:
        self.assertIn("UNVERIFIED", WORKFLOW.read_text(encoding="utf-8").splitlines()[0])

    def test_actions_are_tagged_not_pinned_and_the_file_admits_it(self) -> None:
        """When actions get pinned by commit hash, delete this test and the admission in the header."""
        text = WORKFLOW.read_text(encoding="utf-8")
        uses = re.findall(r"uses:\s*(\S+)", text)
        self.assertGreater(len(uses), 0)
        unpinned = [u for u in uses if not re.search(r"@[0-9a-f]{40}$", u)]
        if unpinned:
            self.assertIn("pin them by commit hash", text)


if __name__ == "__main__":
    unittest.main()
