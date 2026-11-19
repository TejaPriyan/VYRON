"""The generated TypeScript types, the generator's refusals, and the dependency-free guarantee.

Run from the repository root:
    python3 -m unittest discover -s tests
"""

import _paths  # noqa: F401  (puts the tooling directories on sys.path)

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tsgen import UnsupportedSchema, load_spec, prop_key, render_schema, render_typescript, ts_type, type_name

from vyron_api.api import limits
from vyron_api.domain.entities import EntityKind, RelationType
from vyron_api.domain.provenance import Provenance

ROOT = Path(__file__).resolve().parents[1]
SPEC = load_spec(ROOT / "docs" / "openapi.json")
API_TS = ROOT / "packages" / "types" / "src" / "api.ts"
TSC = shutil.which("tsc")
IMPORT = re.compile(r"""(?:^|\n)\s*(?:import|export)\s[^;]*?\sfrom\s+["']([^"']+)["']""")
SKIP_DIRS = {"node_modules", ".next", "dist"}


def source_files(include_tests: bool) -> list[Path]:
    found: list[Path] = []
    for pattern in ("packages/*/src/**/*.ts", "apps/web/src/**/*.ts"):
        for path in ROOT.glob(pattern):
            if SKIP_DIRS & set(path.parts):
                continue
            if path.name.endswith(".test.ts") and not include_tests:
                continue
            found.append(path)
    return sorted(found)


class GeneratedTypesTest(unittest.TestCase):
    def test_api_types_are_current(self) -> None:
        self.assertEqual(
            API_TS.read_text(encoding="utf-8"), render_typescript(SPEC), "run scripts/generate_ts_types.py"
        )

    def test_every_component_and_operation_is_emitted(self) -> None:
        text = API_TS.read_text(encoding="utf-8")
        for name in SPEC["components"]["schemas"]:
            self.assertRegex(text, rf"export (?:interface|type) {type_name(name)}\b", name)
        for item in SPEC["paths"].values():
            for operation in item.values():
                self.assertIn(f"  {operation['operationId']}: {{", text)

    def test_enum_values_match_the_python_domain(self) -> None:
        text = API_TS.read_text(encoding="utf-8")

        def values(const: str) -> list[str]:
            match = re.search(rf"export const {const} = \[(.*?)\] as const;", text)
            assert match, const
            return re.findall(r'"([^"]+)"', match.group(1))

        self.assertEqual(values("ENTITY_KIND_VALUES"), [k.value for k in EntityKind])
        self.assertEqual(values("RELATION_TYPE_VALUES"), [r.value for r in RelationType])
        self.assertEqual(values("PROVENANCE_VALUES"), [p.value for p in Provenance])

    def test_the_language_breakdown_limit_is_a_typed_map_with_its_bound_in_the_comment(self) -> None:
        text = API_TS.read_text(encoding="utf-8")
        self.assertRegex(text, r"/\*\* at most 200 properties \*/\n\s+languages: Record<string, number>;")

    def test_numeric_limits_appear_in_the_types(self) -> None:
        text = API_TS.read_text(encoding="utf-8")
        self.assertIn(f"range 1..{limits.MAX_PAGE_SIZE}; default {limits.DEFAULT_PAGE_SIZE}", text)
        self.assertIn(f"range 1..{limits.MAX_GRAPH_NODES}; default {limits.DEFAULT_GRAPH_NODES}", text)

    def test_every_route_status_is_carried_into_the_route_table(self) -> None:
        planned = re.findall(r'status: "planned"', API_TS.read_text(encoding="utf-8"))
        expected = sum(
            1 for item in SPEC["paths"].values() for op in item.values() if op.get("x-vyron-status") == "planned"
        )
        self.assertEqual(len(planned), expected)
        self.assertGreater(expected, 0)

    def test_error_schema_does_not_shadow_the_global_error(self) -> None:
        text = API_TS.read_text(encoding="utf-8")
        self.assertIn("export interface ApiError", text)
        self.assertNotRegex(text, r"export (?:interface|type) Error\b")


class GeneratorRefusalsTest(unittest.TestCase):
    def test_unsupported_keywords_fail_loudly(self) -> None:
        for schema in (
            {"type": "string", "oneOf": [{"type": "string"}]},
            {"allOf": [{"type": "string"}]},
            {"type": "string", "not": {"enum": ["x"]}},
            {"type": "object", "properties": {}, "patternProperties": {}},
            {"anyOf": [{"type": "string"}, {"type": "integer"}]},  # a union that is not "or null"
            {"anyOf": [{"type": "null"}, {"type": "null"}]},
            {"anyOf": [{"type": "string"}, {"type": "null"}, {"type": "integer"}]},
            {"anyOf": [{"type": "string"}, {"type": "null"}], "type": "string"},
        ):
            with self.assertRaises(UnsupportedSchema, msg=str(schema)):
                ts_type(schema)

    def test_structural_gaps_fail_loudly(self) -> None:
        for schema in (
            {},
            {"type": "array"},
            {"type": "object"},
            {"type": "object", "additionalProperties": True},
            {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": {"type": "string"}},
            {"type": "tuple"},
        ):
            with self.assertRaises(UnsupportedSchema, msg=str(schema)):
                ts_type(schema)

    def test_vendor_extensions_and_constraints_are_tolerated(self) -> None:
        self.assertEqual(ts_type({"type": "string", "x-vyron-status": "planned", "maxLength": 3}), "string")
        bounded = {"type": "object", "additionalProperties": {"type": "integer"}, "maxProperties": 3}
        self.assertEqual(ts_type(bounded), "Record<string, number>")

    def test_a_reference_or_null_is_a_nullable_type_in_either_order(self) -> None:
        ref = {"$ref": "#/components/schemas/Error"}
        self.assertEqual(ts_type({"anyOf": [ref, {"type": "null"}]}), "ApiError | null")
        self.assertEqual(ts_type({"anyOf": [{"type": "null"}, ref]}), "ApiError | null")
        self.assertEqual(ts_type({"anyOf": [ref, {"type": "null"}], "description": "x"}), "ApiError | null")

    def test_scalar_mapping(self) -> None:
        self.assertEqual(ts_type({"type": "integer"}), "number")
        self.assertEqual(ts_type({"type": ["string", "null"]}), "string | null")
        self.assertEqual(ts_type({"type": "boolean"}), "boolean")
        self.assertEqual(ts_type({"const": "pagerank"}), '"pagerank"')
        self.assertEqual(ts_type({"enum": ["a", "b"]}), '"a" | "b"')
        self.assertEqual(ts_type({"$ref": "#/components/schemas/Error"}), "ApiError")

    def test_arrays_and_maps(self) -> None:
        self.assertEqual(ts_type({"type": "array", "items": {"type": "string"}}), "ReadonlyArray<string>")
        counts = {"type": "object", "additionalProperties": {"type": "integer"}}
        self.assertEqual(ts_type(counts), "Record<string, number>")

    def test_optional_properties_follow_required(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a"]}
        out = ts_type(schema)
        self.assertIn("a: string;", out)
        self.assertIn("b?: string;", out)

    def test_property_names_are_quoted_when_needed(self) -> None:
        self.assertEqual(prop_key("snake_case"), "snake_case")
        self.assertEqual(prop_key("kebab-case"), '"kebab-case"')
        self.assertEqual(prop_key("1abc"), '"1abc"')

    def test_comment_terminators_cannot_escape_a_doc_comment(self) -> None:
        out = render_schema("Evil", {"type": "string", "description": "ends */ early"})
        self.assertNotIn("*/ early", out.replace("*\\/ early", ""))
        self.assertEqual(out.count("*/"), 1)

    def test_string_enum_components_also_emit_a_runtime_list(self) -> None:
        out = render_schema("GitHubTargetKind", {"type": "string", "enum": ["owner", "repository"]})
        self.assertIn('export const GITHUB_TARGET_KIND_VALUES = ["owner", "repository"] as const;', out)
        self.assertIn("export type GitHubTargetKind = (typeof GITHUB_TARGET_KIND_VALUES)[number];", out)


class DependencyFreeTest(unittest.TestCase):
    """The TypeScript that this environment can verify imports nothing but itself."""

    def test_sources_import_only_relative_files_with_ts_extensions(self) -> None:
        checked = 0
        for path in source_files(include_tests=True):
            for specifier in IMPORT.findall(path.read_text(encoding="utf-8")):
                allowed = specifier.startswith(("./", "../")) or specifier.startswith("node:")
                self.assertTrue(allowed, f"{path.relative_to(ROOT)} imports {specifier!r}")
                if specifier.startswith((".",)):
                    self.assertTrue(specifier.endswith(".ts"), f"{path.relative_to(ROOT)}: {specifier!r} needs .ts")
                    self.assertTrue((path.parent / specifier).resolve().exists(), f"{path}: {specifier} not found")
                checked += 1
        self.assertGreater(checked, 10)

    def test_non_test_sources_never_import_node_modules(self) -> None:
        for path in source_files(include_tests=False):
            for specifier in IMPORT.findall(path.read_text(encoding="utf-8")):
                self.assertFalse(specifier.startswith("node:"), f"{path.relative_to(ROOT)} imports {specifier}")

    def test_sources_avoid_syntax_that_node_cannot_strip(self) -> None:
        pattern = re.compile(
            r"^\s*(?:export\s+)?(?:const\s+)?enum\s"
            r"|^\s*(?:export\s+)?namespace\s"
            r"|constructor\([^)]*\b(?:public|private|protected|readonly)\s"
        )
        for path in source_files(include_tests=True):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                self.assertIsNone(pattern.search(line), f"{path.relative_to(ROOT)}:{number}: {line.strip()}")

    def test_every_non_test_source_is_covered_by_the_type_check(self) -> None:
        config = (ROOT / "tsconfig.check.json").read_text(encoding="utf-8")
        self.assertIn('"packages/*/src/**/*.ts"', config)
        self.assertIn('"apps/web/src/**/*.ts"', config)
        self.assertIn('"erasableSyntaxOnly": true', config)
        self.assertIn('"strict": true', config)


@unittest.skipUnless(TSC, "tsc is not on PATH")
class TypeCheckTest(unittest.TestCase):
    def test_strict_type_check_passes(self) -> None:
        assert TSC is not None
        result = subprocess.run(
            [TSC, "-p", "tsconfig.check.json"], cwd=ROOT, capture_output=True, text=True, timeout=180, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])


if __name__ == "__main__":
    unittest.main()
