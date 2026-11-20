"""Markdown route reference generated from the registry (spliced into docs/API.md between markers)."""

from __future__ import annotations

from vyron_api.api.routes import ROUTES, Auth, Param, Route, Status

BEGIN = "<!-- BEGIN GENERATED: route reference (scripts/generate_api_docs.py) -->"
END = "<!-- END GENERATED -->"


def _schema_summary(param: Param) -> str:
    schema = param.schema
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[1]
    kind = schema.get("type", "string")
    if kind == "array":
        items = schema.get("items", {})
        inner = items["$ref"].rsplit("/", 1)[1] if "$ref" in items else items.get("type", "string")
        return f"{inner}[] (repeat)"
    parts = [str(kind)]
    if "minimum" in schema or "maximum" in schema:
        parts.append(f"{schema.get('minimum', '')}..{schema.get('maximum', '')}")
    if "maxLength" in schema:
        parts.append(f"≤{schema['maxLength']} chars")
    if schema.get("format"):
        parts.append(str(schema["format"]))
    if "default" in schema:
        parts.append(f"default {str(schema['default']).lower()}")
    return ", ".join(parts)


def _params_cell(route: Route) -> str:
    if not route.params:
        return "-"
    items = []
    for p in route.params:
        mark = "*" if p.required else ""
        items.append(f"`{p.name}`{mark} ({p.location}: {_schema_summary(p)})")
    return "<br>".join(items)


def _row(route: Route) -> str:
    status = "implemented" if route.status is Status.IMPLEMENTED else "**planned**"
    auth = "session" if route.auth is Auth.SESSION else "public"
    cache = f"{route.cache_seconds}s" if route.cache_seconds is not None else "no-store"
    body = route.request_schema or "-"
    result = f"{route.success_status} {route.response_schema or '(empty)'}"
    return (
        f"| `{route.method}` `{route.path}` | {route.summary} | {_params_cell(route)} | {body} | "
        f"{result} | {auth} | {route.rate_bucket.value} | {cache} | {status} |"
    )


def render_route_reference() -> str:
    header = (
        "| Operation | Summary | Parameters (`*` = required) | Request body | Response | Auth | "
        "Rate bucket | Cache | Status |\n|---|---|---|---|---|---|---|---|---|"
    )
    groups = {"Implemented": Status.IMPLEMENTED, "Planned (answer 501 today)": Status.PLANNED}
    blocks = [BEGIN]
    for title, status in groups.items():
        rows = [_row(r) for r in ROUTES if r.status is status]
        blocks.append(f"### {title} ({len(rows)})\n\n{header}\n" + "\n".join(rows))
    blocks.append(END)
    return "\n\n".join(blocks)


def splice(document: str, generated: str) -> str:
    """Replace the generated block in ``document`` (raises if the markers are missing)."""
    start, end = document.find(BEGIN), document.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError("generated-section markers are missing from the document")
    return document[:start] + generated + document[end + len(END) :]
