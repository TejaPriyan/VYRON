import json
import re
import unittest
from pathlib import Path
from typing import Any

from vyron_api.api import limits
from vyron_api.api.errors import ErrorCode
from vyron_api.api.openapi import build_openapi, render_openapi_json
from vyron_api.api.routes import OPERATIONS, ROUTES, Auth, RateBucket, Route, Status
from vyron_api.api.schemas import COMPONENTS, PLANNED_COMPONENTS, obj, string
from vyron_api.api.validate import _ANNOTATIONS, _SUPPORTED
from vyron_api.db.lint import build_schema
from vyron_api.domain.entities import MAX_ALIASES, EntityKind, RelationType
from vyron_api.domain.enums import (
    DatePrecision,
    JobStatus,
    MapMode,
    Reliability,
    ResearchKind,
    ResearchStatus,
    SourceType,
)
from vyron_api.domain.provenance import Provenance

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "infrastructure" / "database" / "migrations"
DOC = build_openapi()


def walk(node: Any):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def resolve(pointer: str) -> Any:
    assert pointer.startswith("#/"), pointer
    node: Any = DOC
    for part in pointer[2:].split("/"):
        node = node[part]
    return node


def referenced_schemas(name: str, seen: set[str] | None = None) -> set[str]:
    seen = seen if seen is not None else set()
    if name in seen:
        return seen
    seen.add(name)
    for node in walk(COMPONENTS[name]):
        if isinstance(node, dict) and "$ref" in node:
            referenced_schemas(node["$ref"].rsplit("/", 1)[1], seen)
    return seen


class RegistryTest(unittest.TestCase):
    def test_operation_ids_are_unique_snake_case(self) -> None:
        ids = [r.operation_id for r in ROUTES]
        self.assertEqual(len(ids), len(set(ids)))
        for operation_id in ids:
            self.assertRegex(operation_id, r"^[a-z][a-z0-9_]*$")

    def test_method_and_path_pairs_are_unique(self) -> None:
        pairs = [(r.method, r.path) for r in ROUTES]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_paths_are_versioned_or_system(self) -> None:
        for route in ROUTES:
            self.assertTrue(
                route.path.startswith("/api/v1/") or route.path.startswith("/health/"), route.path
            )

    def test_no_two_routes_can_match_the_same_request(self) -> None:
        samples = [
            re.sub(r"\{\w+\}", "x", r.path) for r in ROUTES
        ] + ["/api/v1/entities/by-slug/language/python", "/api/v1/entities/x/neighbors"]
        for sample in samples:
            for method in ("GET", "POST", "PUT", "DELETE"):
                matches = [r.operation_id for r in ROUTES if r.method == method and r.match(sample)]
                self.assertLessEqual(len(matches), 1, (method, sample, matches))

    def test_schema_references_exist(self) -> None:
        for route in ROUTES:
            for name in (route.request_schema, route.response_schema):
                if name:
                    self.assertIn(name, COMPONENTS, route.operation_id)

    def test_mutating_routes_are_authenticated_except_auth_endpoints(self) -> None:
        for route in ROUTES:
            if route.method != "GET" and "auth" not in route.tags:
                self.assertIs(route.auth, Auth.SESSION, route.operation_id)

    def test_credential_endpoints_use_the_strictest_bucket(self) -> None:
        for route in ROUTES:
            if route.request_schema == "Credentials":
                self.assertIs(route.rate_bucket, RateBucket.AUTH)
                self.assertEqual(route.method, "POST")

    def test_ai_routes_use_the_ai_bucket(self) -> None:
        for route in ROUTES:
            if route.request_schema == "ResearchRequest":
                self.assertIs(route.rate_bucket, RateBucket.AI)
                self.assertIs(route.auth, Auth.SESSION)

    def test_only_get_routes_are_cacheable_and_authenticated_ones_are_not(self) -> None:
        for route in ROUTES:
            if route.auth is Auth.SESSION:
                self.assertIsNone(route.cache_seconds, route.operation_id)

    def test_constructor_rejects_inconsistent_routes(self) -> None:
        with self.assertRaises(ValueError):
            Route("x", "GET", "/api/v1/a/{id}", "s", ("t",))  # undeclared path param
        with self.assertRaises(ValueError):
            Route("x", "GET", "/api/v1/a", "s", ("t",), request_schema="Entity")
        with self.assertRaises(ValueError):
            Route("x", "POST", "/api/v1/a", "s", ("t",), cache_seconds=5)
        with self.assertRaises(ValueError):
            Route("x", "PATCH", "/api/v1/a", "s", ("t",))

    def test_route_matching_does_not_cross_segments(self) -> None:
        route = OPERATIONS["get_entity"]
        self.assertIsNone(route.match("/api/v1/entities/a/b"))
        self.assertIsNone(route.match("/api/v1/entities/"))
        self.assertEqual(route.match("/api/v1/entities/abc"), {"entity_id": "abc"})


class OpenApiStructureTest(unittest.TestCase):
    def test_header(self) -> None:
        self.assertEqual(DOC["openapi"], "3.1.0")
        self.assertEqual(DOC["info"]["title"], "VYRON API")

    def test_every_ref_resolves(self) -> None:
        count = 0
        for node in walk(DOC):
            if isinstance(node, dict) and "$ref" in node:
                resolve(node["$ref"])
                count += 1
        self.assertGreater(count, 100)

    def test_all_routes_are_published(self) -> None:
        published = {
            op["operationId"]: (method, path)
            for path, item in DOC["paths"].items()
            for method, op in item.items()
        }
        self.assertEqual(set(published), {r.operation_id for r in ROUTES})
        for route in ROUTES:
            self.assertEqual(published[route.operation_id], (route.method.lower(), route.path))

    def test_path_parameters_match_templates_and_are_required(self) -> None:
        for path, item in DOC["paths"].items():
            for operation in item.values():
                declared = {p["name"] for p in operation.get("parameters", []) if p["in"] == "path"}
                self.assertEqual(declared, set(re.findall(r"\{(\w+)\}", path)), operation["operationId"])
                for param in operation.get("parameters", []):
                    if param["in"] == "path":
                        self.assertTrue(param["required"])

    def test_every_operation_documents_success_and_errors(self) -> None:
        for item in DOC["paths"].values():
            for operation in item.values():
                responses = operation["responses"]
                self.assertTrue(any(code.startswith("2") for code in responses), operation["operationId"])
                self.assertIn("429", responses)
                self.assertIn("500", responses)
                for code, response in responses.items():
                    if "$ref" not in response:
                        self.assertIn("description", response, (operation["operationId"], code))

    def test_error_responses_use_the_error_schema(self) -> None:
        for name, response in DOC["components"]["responses"].items():
            self.assertEqual(
                response["content"]["application/json"]["schema"], {"$ref": "#/components/schemas/Error"}, name
            )

    def test_security_matches_auth(self) -> None:
        for route in ROUTES:
            operation = DOC["paths"][route.path][route.method.lower()]
            expected = [{"sessionCookie": []}] if route.auth is Auth.SESSION else []
            self.assertEqual(operation["security"], expected, route.operation_id)
            if route.auth is Auth.SESSION:
                self.assertIn("401", operation["responses"])

    def test_planned_operations_are_marked_everywhere(self) -> None:
        for route in ROUTES:
            operation = DOC["paths"][route.path][route.method.lower()]
            self.assertEqual(operation["x-vyron-status"], route.status.value)
            if route.status is Status.PLANNED:
                self.assertTrue(operation["description"].startswith("PLANNED"), route.operation_id)
                self.assertIn("501", operation["responses"])
            else:
                self.assertNotIn("501", operation["responses"])

    def test_planned_components_are_marked_and_only_used_by_planned_routes(self) -> None:
        for name in PLANNED_COMPONENTS:
            self.assertEqual(DOC["components"]["schemas"][name]["x-vyron-status"], "planned")
        implemented_closure: set[str] = set()
        for route in ROUTES:
            if route.status is Status.IMPLEMENTED:
                for name in (route.request_schema, route.response_schema):
                    if name:
                        implemented_closure |= referenced_schemas(name)
        self.assertEqual(implemented_closure & PLANNED_COMPONENTS, set())

    def test_array_query_parameters_use_repeated_form_style(self) -> None:
        for item in DOC["paths"].values():
            for operation in item.values():
                for param in operation.get("parameters", []):
                    if param["schema"].get("type") == "array":
                        self.assertEqual((param["style"], param["explode"]), ("form", True))

    def test_only_supported_schema_keywords_are_used(self) -> None:
        allowed = _SUPPORTED | _ANNOTATIONS

        def check(schema: dict[str, Any], where: str) -> None:
            self.assertEqual(set(schema) - allowed, set(), where)
            for child in schema.get("properties", {}).values():
                check(child, f"{where}.properties")
            for key in ("items", "additionalProperties"):
                if isinstance(schema.get(key), dict):
                    check(schema[key], f"{where}.{key}")
            for key in ("anyOf", "oneOf", "allOf"):
                for child in schema.get(key, ()):
                    check(child, f"{where}.{key}")

        for name, schema in COMPONENTS.items():
            check(schema, name)

    def test_every_object_is_closed_or_has_typed_additional_properties(self) -> None:
        for name, schema in COMPONENTS.items():
            for node in walk(schema):
                if isinstance(node, dict) and node.get("type") == "object":
                    self.assertIn("additionalProperties", node, name)

    def test_required_lists_only_name_declared_properties(self) -> None:
        for name, schema in COMPONENTS.items():
            for node in walk(schema):
                if isinstance(node, dict) and node.get("type") == "object" and "properties" in node:
                    self.assertLessEqual(set(node["required"]), set(node["properties"]), name)

    def test_obj_helper_rejects_unknown_optional_names(self) -> None:
        with self.assertRaises(ValueError):
            obj({"a": string()}, optional=("b",))

    def test_generated_file_is_current(self) -> None:
        committed = (ROOT / "docs" / "openapi.json").read_text(encoding="utf-8")
        self.assertEqual(committed, render_openapi_json(), "run scripts/generate_openapi.py")
        json.loads(committed)

    def test_published_vocabulary_matches_the_domain(self) -> None:
        pairs = {
            "EntityKind": EntityKind, "RelationType": RelationType, "Provenance": Provenance,
            "DatePrecision": DatePrecision, "MapMode": MapMode, "ResearchKind": ResearchKind,
            "ResearchStatus": ResearchStatus, "JobStatus": JobStatus, "SourceType": SourceType,
            "Reliability": Reliability, "ErrorCode": ErrorCode,
        }  # fmt: skip
        for name, enum in pairs.items():
            self.assertEqual(COMPONENTS[name]["enum"], [m.value for m in enum], name)

    def test_published_vocabulary_matches_the_sql_enums(self) -> None:
        files = [(p.name, p.read_text(encoding="utf-8")) for p in sorted(MIGRATIONS.glob("*.sql"))]
        schema = build_schema(files)
        sql_names = {
            "EntityKind": "entity_kind", "RelationType": "relation_type", "Provenance": "provenance",
            "DatePrecision": "date_precision", "MapMode": "map_mode", "ResearchKind": "research_kind",
            "ResearchStatus": "research_status", "JobStatus": "job_status", "SourceType": "source_type",
            "Reliability": "reliability",
        }  # fmt: skip
        for component, sql_name in sql_names.items():
            self.assertEqual(
                sorted(COMPONENTS[component]["enum"]), sorted(schema.enums[sql_name]), component
            )


class ResponseSafetyTest(unittest.TestCase):
    """Responses must never describe secrets or internal storage details."""

    FORBIDDEN = re.compile(r"password|passwd|token|secret|api_?key|hash|salt|credential", re.I)

    def test_no_response_schema_has_secret_like_property_names(self) -> None:
        for route in ROUTES:
            if not route.response_schema:
                continue
            for name in referenced_schemas(route.response_schema):
                for node in walk(COMPONENTS[name]):
                    if isinstance(node, dict) and "properties" in node:
                        for prop in node["properties"]:
                            self.assertIsNone(self.FORBIDDEN.search(prop), (route.operation_id, name, prop))

    def test_password_is_write_only_and_bounded(self) -> None:
        password = COMPONENTS["Credentials"]["properties"]["password"]
        self.assertGreaterEqual(password["minLength"], 12)
        self.assertLessEqual(password["maxLength"], 128)  # bounded: hashing cost is a DoS vector
        self.assertNotIn("password", json.dumps(COMPONENTS["UserProfile"]))

    def test_every_array_an_implemented_route_returns_declares_a_bound(self) -> None:
        checked = 0
        for route in ROUTES:
            if route.status is not Status.IMPLEMENTED or not route.response_schema:
                continue
            for name in referenced_schemas(route.response_schema):
                for node in walk(COMPONENTS[name]):
                    if isinstance(node, dict) and node.get("type") == "array":
                        self.assertIn("maxItems", node, (route.operation_id, name))
                        checked += 1
        self.assertGreater(checked, 15)

    def test_collection_bounds_match_the_request_limits(self) -> None:
        props = COMPONENTS["EntityList"]["properties"]["items"]
        self.assertEqual(props["maxItems"], limits.MAX_PAGE_SIZE)
        self.assertEqual(COMPONENTS["TimelineList"]["properties"]["items"]["maxItems"], limits.MAX_PAGE_SIZE)
        self.assertEqual(COMPONENTS["Subgraph"]["properties"]["entities"]["maxItems"], limits.MAX_GRAPH_NODES)
        self.assertEqual(COMPONENTS["Subgraph"]["properties"]["relationships"]["maxItems"], limits.MAX_GRAPH_EDGES)
        self.assertEqual(COMPONENTS["Entity"]["properties"]["aliases"]["maxItems"], MAX_ALIASES)


if __name__ == "__main__":
    unittest.main()
