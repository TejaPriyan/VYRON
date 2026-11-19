"""What can be checked about modules that need packages this environment cannot install.

``fastapi_app`` has never been imported. These tests prove only what the standard library can: the
file parses, it imports nothing third-party except the expected packages, and every name it takes
from VYRON's own modules actually exists. They do NOT show that the FastAPI usage is correct.
"""

import ast
import importlib
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "vyron_api"
UNVERIFIED = {"fastapi_app.py": {"fastapi"}}  # module -> third-party packages it may import


class UnverifiedModulesTest(unittest.TestCase):
    def parse(self, name: str) -> ast.Module:
        return ast.parse((SRC / name).read_text(encoding="utf-8"), filename=name)

    def test_files_parse(self) -> None:
        for name in UNVERIFIED:
            self.parse(name)

    def test_only_expected_third_party_packages_are_imported(self) -> None:
        stdlib = set(__import__("sys").stdlib_module_names)
        for name, allowed in UNVERIFIED.items():
            for node in ast.walk(self.parse(name)):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules = [node.module]
                for module in modules:
                    top = module.split(".")[0]
                    self.assertTrue(
                        top in stdlib or top == "vyron_api" or top in allowed or top == "__future__",
                        f"{name} imports unexpected package {top}",
                    )

    def test_every_vyron_name_it_imports_exists(self) -> None:
        checked = 0
        for name in UNVERIFIED:
            for node in ast.walk(self.parse(name)):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("vyron_api"):
                    module = importlib.import_module(node.module)
                    for alias in node.names:
                        self.assertTrue(
                            hasattr(module, alias.name), f"{name}: {node.module}.{alias.name} does not exist"
                        )
                        checked += 1
        self.assertGreater(checked, 5)

    def test_the_module_says_it_is_unverified(self) -> None:
        for name in UNVERIFIED:
            self.assertIn("UNVERIFIED", (SRC / name).read_text(encoding="utf-8")[:400])

    def test_declared_dependencies_cover_the_third_party_imports(self) -> None:
        pyproject = (SRC.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        for allowed in UNVERIFIED.values():
            for package in allowed:
                self.assertIn(package, pyproject)


if __name__ == "__main__":
    unittest.main()
