"""Parse and validate path and query parameters against the route's own schemas.

The same schema dicts that are published in OpenAPI drive parsing, so documentation and behaviour
cannot diverge. All problems are collected and reported together as one ``validation_error``.
Unknown query parameters are rejected (a typo should fail loudly, not be silently ignored), repeated
scalars are rejected, and integers/booleans are parsed strictly (ASCII digits, ``true``/``false``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs

from vyron_api.api.errors import FieldIssue, validation_error
from vyron_api.api.limits import MAX_QUERY_STRING_BYTES
from vyron_api.api.routes import Param, Route
from vyron_api.api.schemas import COMPONENTS, Schema
from vyron_api.api.validate import validate

_INT_RE = re.compile(r"^-?[0-9]{1,18}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_QUERY_FIELDS = 30


def _resolve(schema: Schema) -> Schema:
    while "$ref" in schema:
        schema = COMPONENTS[schema["$ref"].rsplit("/", 1)[1]]
    return schema


def _base_type(schema: Schema) -> str:
    kind = _resolve(schema).get("type", "string")
    if isinstance(kind, list):
        kind = next(t for t in kind if t != "null")
    return str(kind)


def _coerce(text: str, schema: Schema) -> Any:
    """Convert one raw string to the schema's type, or raise ``ValueError`` with a reason."""
    kind = _base_type(schema)
    if kind == "integer":
        if not _INT_RE.fullmatch(text):
            raise ValueError("must be an integer")
        return int(text)
    if kind == "boolean":
        if text not in {"true", "false"}:
            raise ValueError("must be 'true' or 'false'")
        return text == "true"
    if _CONTROL_RE.search(text):
        raise ValueError("contains control characters")
    return text


def _parse_param(param: Param, values: list[str], issues: list[FieldIssue]) -> Any:
    schema = _resolve(param.schema)
    is_array = _base_type(schema) == "array"
    if is_array:
        item_schema = schema.get("items", {"type": "string"})
        parsed: Any = []
        for text in values:
            try:
                parsed.append(_coerce(text, item_schema))
            except ValueError as exc:
                issues.append(FieldIssue(param.name, str(exc)))
                return None
    else:
        if len(values) > 1:
            issues.append(FieldIssue(param.name, "must be given only once"))
            return None
        try:
            parsed = _coerce(values[0], param.schema)
        except ValueError as exc:
            issues.append(FieldIssue(param.name, str(exc)))
            return None
    problems = validate(parsed, param.schema, COMPONENTS)
    for problem in problems:
        issues.append(FieldIssue(param.name, problem.message))
    return None if problems else parsed


def parse_path_params(route: Route, raw: Mapping[str, str]) -> dict[str, Any]:
    issues: list[FieldIssue] = []
    result: dict[str, Any] = {}
    for param in route.path_params:
        value = _parse_param(param, [raw[param.name]], issues)
        if value is not None:
            result[param.name] = value
    if issues:
        raise validation_error(issues, "invalid path parameter")
    return result


def parse_query_params(route: Route, query_string: str) -> dict[str, Any]:
    """Validated query values by parameter name; omitted parameters get their default (or None)."""
    if len(query_string.encode("utf-8", "replace")) > MAX_QUERY_STRING_BYTES:
        raise validation_error([FieldIssue("query", "query string is too long")])
    try:
        raw = parse_qs(
            query_string, keep_blank_values=True, max_num_fields=MAX_QUERY_FIELDS, errors="strict"
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise validation_error([FieldIssue("query", "query string is malformed")]) from exc

    issues: list[FieldIssue] = []
    known = {param.name for param in route.query_params}
    for name in sorted(set(raw) - known):
        issues.append(FieldIssue(name[:60], "unknown query parameter"))

    result: dict[str, Any] = {}
    for param in route.query_params:
        values = raw.get(param.name)
        if values is None:
            if param.required:
                issues.append(FieldIssue(param.name, "is required"))
            default = _resolve(param.schema).get("default")
            result[param.name] = default
            continue
        result[param.name] = _parse_param(param, values, issues)
    if issues:
        raise validation_error(issues, "invalid query parameters")
    return result
