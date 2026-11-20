import unittest
from pathlib import Path

from vyron_api.api import limits
from vyron_api.api.apidocs import BEGIN, END, render_route_reference, splice
from vyron_api.api.cursor import DEFAULT_TTL_SECONDS
from vyron_api.api.errors import ErrorCode
from vyron_api.api.routes import ROUTES, Status

DOC = (Path(__file__).resolve().parents[3] / "docs" / "API.md").read_text(encoding="utf-8")


class ApiDocsTest(unittest.TestCase):
    def test_route_reference_is_current(self) -> None:
        self.assertEqual(splice(DOC, render_route_reference()), DOC, "run scripts/generate_api_docs.py")

    def test_every_route_is_listed_once(self) -> None:
        for route in ROUTES:
            self.assertEqual(DOC.count(f"`{route.method}` `{route.path}`"), 1, route.operation_id)

    def test_planned_routes_are_flagged(self) -> None:
        for route in ROUTES:
            line = next(line for line in DOC.splitlines() if f"`{route.method}` `{route.path}`" in line)
            self.assertEqual("**planned**" in line, route.status is Status.PLANNED, route.operation_id)

    def test_every_error_code_is_documented(self) -> None:
        for code in ErrorCode:
            self.assertIn(f"| `{code.value}` |", DOC)

    def test_documented_numbers_match_the_code(self) -> None:
        self.assertIn(f"`limit` (1\u2013{limits.MAX_PAGE_SIZE}, default {limits.DEFAULT_PAGE_SIZE})", DOC)
        self.assertIn(f"{limits.MAX_GRAPH_NODES} nodes and {limits.MAX_GRAPH_EDGES:,} relationships", DOC)
        self.assertIn(f"Body over {limits.MAX_BODY_BYTES // 1024} KiB", DOC)
        self.assertIn(f"expire after {DEFAULT_TTL_SECONDS // 3600} hours", DOC)

    def test_unverified_parts_are_called_out(self) -> None:
        self.assertIn("UNVERIFIED", DOC)
        self.assertIn("NOT WRITTEN", DOC)

    def test_splice_requires_markers(self) -> None:
        with self.assertRaises(ValueError):
            splice("no markers here", "x")
        self.assertEqual(splice(f"a{BEGIN}old{END}b", "NEW"), "aNEWb")


if __name__ == "__main__":
    unittest.main()
