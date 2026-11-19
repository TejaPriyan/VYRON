"""A small JSON Schema validator for the subset VYRON's contract uses (standard library only).

It exists so the API's own schemas are executable: tests validate real serializer output against the
same schemas that are published in OpenAPI, and query parameters are parsed with them. It is not a
general validator. Supported keywords: ``$ref`` (local ``#/components/schemas/..``), ``type`` (string
or list), ``enum``, ``const``, ``properties``, ``required``, ``additionalProperties`` (bool or
schema), ``maxProperties``, ``items``, ``minItems``, ``maxItems``, ``uniqueItems``, ``minLength``,
``maxLength``, ``pattern``, ``minimum``, ``maximum``, ``anyOf``, ``oneOf``, ``allOf`` and ``format``
(uuid, date, date-time, uri). Any other keyword other than annotations raises, so a schema that relies on
something unsupported fails loudly instead of silently validating nothing.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

_ANNOTATIONS = frozenset(
    {
        "description", "title", "default", "examples", "example", "deprecated", "readOnly",
        "writeOnly", "x-vyron-note", "x-vyron-status",
    }
)  # fmt: skip
_SUPPORTED = frozenset(
    {
        "$ref", "type", "enum", "const", "properties", "required", "additionalProperties", "items",
        "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum",
        "maximum", "anyOf", "oneOf", "allOf", "format", "maxProperties",
    }
)  # fmt: skip
_REF_PREFIX = "#/components/schemas/"
_MAX_DEPTH = 32


class UnsupportedSchemaError(ValueError):
    """The schema uses a keyword this validator does not implement."""


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    path: str
    message: str


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    raise UnsupportedSchemaError(f"unknown type {expected!r}")


def _format_ok(value: str, name: str) -> bool:
    try:
        if name == "uuid":
            return str(uuid.UUID(value)) == value.lower()
        if name == "date":
            return date.fromisoformat(value).isoformat() == value
        if name == "date-time":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value.endswith("Z") or "+" in value[10:] or "-" in value[10:]
        if name == "uri":
            parts = urlsplit(value)
            return bool(parts.scheme and parts.netloc)
    except ValueError:
        return False
    raise UnsupportedSchemaError(f"unsupported format {name!r}")


def validate(
    instance: Any, schema: Mapping[str, Any], components: Mapping[str, Mapping[str, Any]]
) -> list[SchemaIssue]:
    """Return every way ``instance`` violates ``schema`` (empty means valid)."""
    issues: list[SchemaIssue] = []
    _check(instance, schema, components, "$", issues, 0)
    return issues


def _check(
    value: Any,
    schema: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    path: str,
    issues: list[SchemaIssue],
    depth: int,
) -> None:
    if depth > _MAX_DEPTH:
        raise UnsupportedSchemaError("schema nesting is too deep (cyclic $ref?)")
    unknown = set(schema) - _SUPPORTED - _ANNOTATIONS
    if unknown:
        raise UnsupportedSchemaError(f"unsupported schema keywords at {path}: {sorted(unknown)}")

    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith(_REF_PREFIX):
            raise UnsupportedSchemaError(f"unsupported $ref {ref!r}")
        name = ref[len(_REF_PREFIX) :]
        if name not in components:
            raise UnsupportedSchemaError(f"unresolved $ref {ref!r}")
        _check(value, components[name], components, path, issues, depth + 1)
        return

    for combinator in ("allOf",):
        for sub in schema.get(combinator, ()):
            _check(value, sub, components, path, issues, depth + 1)
    if "anyOf" in schema:
        if not any(not _sub_issues(value, sub, components, path, depth) for sub in schema["anyOf"]):
            issues.append(SchemaIssue(path, "does not match any allowed shape"))
    if "oneOf" in schema:
        matches = sum(1 for sub in schema["oneOf"] if not _sub_issues(value, sub, components, path, depth))
        if matches != 1:
            issues.append(SchemaIssue(path, f"must match exactly one allowed shape (matched {matches})"))

    if "type" in schema:
        expected = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_matches(value, t) for t in expected):
            issues.append(SchemaIssue(path, f"expected {' or '.join(expected)}"))
            return
    if "const" in schema and value != schema["const"]:
        issues.append(SchemaIssue(path, f"must equal {schema['const']!r}"))
    if "enum" in schema and value not in schema["enum"]:
        issues.append(SchemaIssue(path, "is not one of the allowed values"))

    if isinstance(value, str):
        _check_string(value, schema, path, issues)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            issues.append(SchemaIssue(path, f"must be >= {schema['minimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            issues.append(SchemaIssue(path, f"must be <= {schema['maximum']}"))
    elif isinstance(value, list):
        _check_array(value, schema, components, path, issues, depth)
    elif isinstance(value, dict):
        _check_object(value, schema, components, path, issues, depth)


def _sub_issues(
    value: Any, sub: Mapping[str, Any], components: Mapping[str, Mapping[str, Any]], path: str, depth: int
) -> list[SchemaIssue]:
    found: list[SchemaIssue] = []
    _check(value, sub, components, path, found, depth + 1)
    return found


def _check_string(value: str, schema: Mapping[str, Any], path: str, issues: list[SchemaIssue]) -> None:
    if "minLength" in schema and len(value) < schema["minLength"]:
        minimum = schema["minLength"]
        message = "must not be empty" if minimum == 1 else f"must be at least {minimum} characters"
        issues.append(SchemaIssue(path, message))
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        issues.append(SchemaIssue(path, f"must be at most {schema['maxLength']} characters"))
    if "pattern" in schema and not re.search(schema["pattern"], value):
        issues.append(SchemaIssue(path, "has an invalid format"))
    if "format" in schema and not _format_ok(value, schema["format"]):
        issues.append(SchemaIssue(path, f"is not a valid {schema['format']}"))


def _check_array(
    value: Sequence[Any],
    schema: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    path: str,
    issues: list[SchemaIssue],
    depth: int,
) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        issues.append(SchemaIssue(path, f"must have at least {schema['minItems']} items"))
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        issues.append(SchemaIssue(path, f"must have at most {schema['maxItems']} items"))
    if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
        issues.append(SchemaIssue(path, "items must be unique"))
    if "items" in schema:
        for index, item in enumerate(value):
            _check(item, schema["items"], components, f"{path}[{index}]", issues, depth + 1)


def _check_object(
    value: Mapping[str, Any],
    schema: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    path: str,
    issues: list[SchemaIssue],
    depth: int,
) -> None:
    properties = schema.get("properties", {})
    maximum = schema.get("maxProperties")
    if maximum is not None and len(value) > maximum:
        issues.append(SchemaIssue(path, f"has more than {maximum} properties"))
    for name in schema.get("required", ()):
        if name not in value:
            issues.append(SchemaIssue(f"{path}.{name}", "is required"))
    additional = schema.get("additionalProperties", True)
    for name, item in value.items():
        child = f"{path}.{name}"
        if name in properties:
            _check(item, properties[name], components, child, issues, depth + 1)
        elif additional is False:
            issues.append(SchemaIssue(child, "is not an allowed property"))
        elif isinstance(additional, Mapping):
            _check(item, additional, components, child, issues, depth + 1)
