"""Static consistency checks for the SQL migrations. No database needed.

This is NOT a substitute for running the migrations on PostgreSQL (that remains UNVERIFIED until a
server is available). It catches the mistakes a human or a model makes when writing DDL blind:
foreign keys to missing tables or columns, mismatched column types, unindexed foreign keys,
enum values that drifted from the Python domain, missing ``updated_at`` triggers, secrets stored
under non-hash names, and unique constraints that break soft deletion.

It understands only the subset of DDL our migrations use; any statement outside that subset is
reported instead of silently skipped, so the linter cannot rot into a rubber stamp.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from vyron_api.db.sqlsplit import SqlSyntaxError, split_sql_statements
from vyron_api.domain.entities import EntityKind, RelationType
from vyron_api.domain.enums import (
    DatePrecision,
    MapMode,
    Reliability,
    ResearchKind,
    ResearchStatus,
    SourceType,
)
from vyron_api.domain.provenance import Provenance

# SQL enum name -> Python enum that must have exactly the same values.
ENUM_MIRRORS: dict[str, type[Enum]] = {
    "entity_kind": EntityKind,
    "relation_type": RelationType,
    "provenance": Provenance,
    "source_type": SourceType,
    "reliability": Reliability,
    "date_precision": DatePrecision,
    "map_mode": MapMode,
    "research_kind": ResearchKind,
    "research_status": ResearchStatus,
}

BUILTIN_TYPES = frozenset(
    {
        "uuid", "text", "boolean", "smallint", "integer", "bigint", "real", "numeric", "date",
        "timestamptz", "bytea", "jsonb", "char", "varchar", "double precision",
    }
)  # fmt: skip

# Tables that legitimately have no created_at/updated_at pair, with the reason.
NO_TIMESTAMP_TABLES = {
    "repository_languages": "junction with its own measured_at",
    "relationship_evidence": "pure junction",
    "ai_research_run_entities": "pure junction",
    "ai_research_run_evidence": "pure junction",
    "ai_research_claim_evidence": "pure junction",
    "api_usage": "append-only log with occurred_at",
}
# Append-only tables: created_at without updated_at is intended.
APPEND_ONLY_TABLES = {"entity_aliases", "search_history", "ai_research_claims"}

_STOP_RE = re.compile(
    r"\b(NOT|NULL|DEFAULT|PRIMARY|REFERENCES|CHECK|UNIQUE|GENERATED|CONSTRAINT|COLLATE)\b",
    re.IGNORECASE,
)
_SECRET_NAME_RE = re.compile(r"(password|token|secret|api_?key)", re.IGNORECASE)
_SOFT_DELETE_PREDICATE_RE = re.compile(r"\bdeleted_at\s+IS\s+NULL\b", re.IGNORECASE)
_PLAIN_COLUMN_RE = re.compile(r"^(\w+)(?:\s+(?:ASC|DESC))?$", re.IGNORECASE)


@dataclass
class Column:
    name: str
    type_name: str
    is_array: bool
    not_null: bool
    default: str | None
    inline_pk: bool
    inline_unique: bool
    inline_ref: tuple[str, tuple[str, ...] | None] | None
    inline_on_delete: str | None = None


@dataclass
class ForeignKey:
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...] | None
    on_delete: str | None = None  # CASCADE, RESTRICT, SET NULL, ...; None means it was not stated


@dataclass
class Table:
    name: str
    order: int
    migration: str
    columns: dict[str, Column] = field(default_factory=dict)
    pk: tuple[str, ...] = ()
    uniques: list[tuple[str, ...]] = field(default_factory=list)
    fks: list[ForeignKey] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)


@dataclass
class Index:
    name: str
    table: str
    columns: tuple[str, ...]
    unique: bool
    predicate: str | None


@dataclass
class Schema:
    enums: dict[str, tuple[str, ...]] = field(default_factory=dict)
    tables: dict[str, Table] = field(default_factory=dict)
    indexes: dict[str, Index] = field(default_factory=dict)
    triggers: list[tuple[str, str, str]] = field(default_factory=list)  # name, table, function
    functions: set[str] = field(default_factory=set)
    views: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)


# ---- small text helpers ---------------------------------------------------------------------
def _matching_paren(text: str, open_index: int) -> int:
    depth, i, quote = 0, open_index, ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise SqlSyntaxError("unbalanced parentheses")


def _split_top_level(text: str) -> list[str]:
    parts, depth, quote, start = [], 0, "", 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _names(text: str) -> tuple[str, ...]:
    return tuple(part.strip().strip('"').lower() for part in text.split(",") if part.strip())


_ON_DELETE_RE = re.compile(r"\bON\s+DELETE\s+(CASCADE|RESTRICT|NO\s+ACTION|SET\s+NULL|SET\s+DEFAULT)\b", re.I)


def _on_delete(text: str) -> str | None:
    match = _ON_DELETE_RE.search(text)
    return re.sub(r"\s+", " ", match.group(1).upper()) if match else None


def _checks_in(text: str) -> list[str]:
    found = []
    for match in re.finditer(r"\bCHECK\s*\(", text, re.IGNORECASE):
        start = match.end() - 1
        found.append(text[start + 1 : _matching_paren(text, start)])
    return found


# ---- parsing --------------------------------------------------------------------------------
def _parse_column(item: str) -> Column:
    match = re.match(r'"?([A-Za-z_][A-Za-z0-9_]*)"?\s+(.*)$', item, re.DOTALL)
    if not match:
        raise SqlSyntaxError(f"cannot parse column definition: {item[:50]!r}")
    name, rest = match.group(1).lower(), match.group(2)
    stop = _STOP_RE.search(rest)
    type_text = (rest[: stop.start()] if stop else rest).strip()
    base = re.sub(r"\(.*\)", "", type_text).strip().lower()
    is_array = base.endswith("[]")
    if is_array:
        base = base[:-2].strip()
    default_match = re.search(
        r"\bDEFAULT\s+(.+?)(?=\s+(?:NOT|NULL|PRIMARY|REFERENCES|CHECK|UNIQUE|GENERATED|CONSTRAINT|COLLATE)\b|$)",
        rest,
        re.IGNORECASE | re.DOTALL,
    )
    ref_match = re.search(r"\bREFERENCES\s+(\w+)\s*(?:\(([^)]*)\))?", rest, re.IGNORECASE)
    ref = None
    if ref_match:
        ref = (ref_match.group(1).lower(), _names(ref_match.group(2)) if ref_match.group(2) else None)
    return Column(
        name=name,
        type_name=base,
        is_array=is_array,
        not_null=bool(re.search(r"\bNOT\s+NULL\b", rest, re.IGNORECASE)),
        default=default_match.group(1).strip() if default_match else None,
        inline_pk=bool(re.search(r"\bPRIMARY\s+KEY\b", rest, re.IGNORECASE)),
        inline_unique=bool(re.search(r"\bUNIQUE\b", rest, re.IGNORECASE)),
        inline_ref=ref,
        inline_on_delete=_on_delete(rest) if ref_match else None,
    )


def _parse_create_table(schema: Schema, statement: str, migration: str) -> None:
    match = re.match(r"CREATE\s+TABLE\s+(\w+)\s*\(", statement, re.IGNORECASE)
    assert match
    name = match.group(1).lower()
    open_index = match.end() - 1
    body = statement[open_index + 1 : _matching_paren(statement, open_index)]
    if name in schema.tables:
        schema.problems.append(f"table {name} is created twice")
    table = Table(name, len(schema.tables), migration)
    for item in _split_top_level(body):
        constraint = re.sub(r"^CONSTRAINT\s+\w+\s+", "", item, flags=re.IGNORECASE)
        keyword = constraint.split(None, 1)[0].upper() if constraint else ""
        if keyword in {"PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "EXCLUDE"}:
            table.checks.extend(_checks_in(constraint))
            if keyword == "PRIMARY":
                cols = re.search(r"PRIMARY\s+KEY\s*\(([^)]*)\)", constraint, re.IGNORECASE)
                table.pk = _names(cols.group(1)) if cols else ()
            elif keyword == "UNIQUE":
                cols = re.search(r"UNIQUE\s*\(([^)]*)\)", constraint, re.IGNORECASE)
                if cols:
                    table.uniques.append(_names(cols.group(1)))
            elif keyword == "FOREIGN":
                fk = re.search(
                    r"FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)\s*\(([^)]*)\)",
                    constraint,
                    re.IGNORECASE,
                )
                if fk:
                    table.fks.append(
                        ForeignKey(
                            _names(fk.group(1)),
                            fk.group(2).lower(),
                            _names(fk.group(3)),
                            _on_delete(constraint[fk.end() :]),
                        )
                    )
                else:
                    schema.problems.append(f"{name}: unparsable FOREIGN KEY clause")
            continue
        column = _parse_column(item)
        table.checks.extend(_checks_in(item))
        if column.name in table.columns:
            schema.problems.append(f"{name}.{column.name} is defined twice")
        table.columns[column.name] = column
        if column.inline_pk:
            table.pk = (column.name,)
        if column.inline_unique:
            table.uniques.append((column.name,))
        if column.inline_ref:
            ref_table, ref_cols = column.inline_ref
            table.fks.append(ForeignKey((column.name,), ref_table, ref_cols, column.inline_on_delete))
    schema.tables[name] = table


def _parse_create_index(schema: Schema, statement: str) -> None:
    head = re.match(
        r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(\w+)\s+ON\s+(\w+)(?:\s+USING\s+\w+)?\s*\(",
        statement,
        re.IGNORECASE,
    )
    if not head:
        schema.problems.append(f"unparsable CREATE INDEX: {statement[:60]!r}")
        return
    open_index = head.end() - 1
    close = _matching_paren(statement, open_index)
    rest = statement[close + 1 :].strip()
    predicate_match = re.match(r"WHERE\s+(.*)$", rest, re.IGNORECASE | re.DOTALL)
    if rest and not predicate_match:
        schema.problems.append(f"index {head.group(2)}: unexpected trailing clause {rest[:30]!r}")
    name = head.group(2).lower()
    if name in schema.indexes:
        schema.problems.append(f"index {name} is created twice")
    schema.indexes[name] = Index(
        name=name,
        table=head.group(3).lower(),
        columns=tuple(_split_top_level(statement[open_index + 1 : close])),
        unique=bool(head.group(1)),
        predicate=predicate_match.group(1).strip() if predicate_match else None,
    )


def parse_migration(schema: Schema, filename: str, sql: str) -> None:
    try:
        statements = split_sql_statements(sql)
    except SqlSyntaxError as exc:
        schema.problems.append(f"{filename}: {exc}")
        return
    for statement in statements:
        try:
            _parse_statement(schema, filename, statement)
        except SqlSyntaxError as exc:
            schema.problems.append(f"{filename}: {exc}")


def _parse_statement(schema: Schema, filename: str, statement: str) -> None:
    if re.match(r"CREATE\s+EXTENSION\b", statement, re.IGNORECASE):
        return
    if match := re.match(r"CREATE\s+TYPE\s+(\w+)\s+AS\s+ENUM\s*\((.*)\)\s*$", statement, re.I | re.S):
        values = tuple(re.findall(r"'([^']*)'", match.group(2)))
        name = match.group(1).lower()
        if name in schema.enums:
            schema.problems.append(f"enum {name} is created twice")
        if len(set(values)) != len(values):
            schema.problems.append(f"enum {name} has duplicate values")
        schema.enums[name] = values
    elif re.match(r"CREATE\s+TABLE\s+\w+\s*\(", statement, re.IGNORECASE):
        _parse_create_table(schema, statement, filename)
    elif re.match(r"CREATE\s+(UNIQUE\s+)?INDEX\b", statement, re.IGNORECASE):
        _parse_create_index(schema, statement)
    elif match := re.match(
        r"CREATE\s+TRIGGER\s+(\w+)\s+BEFORE\s+UPDATE\s+ON\s+(\w+)\s+FOR\s+EACH\s+ROW\s+"
        r"EXECUTE\s+(?:FUNCTION|PROCEDURE)\s+(\w+)\s*\(\s*\)\s*$",
        statement,
        re.IGNORECASE,
    ):
        schema.triggers.append((match.group(1).lower(), match.group(2).lower(), match.group(3).lower()))
    elif match := re.match(r"CREATE\s+FUNCTION\s+(\w+)\s*\(", statement, re.IGNORECASE):
        schema.functions.add(match.group(1).lower())
    elif match := re.match(r"CREATE\s+VIEW\s+(\w+)\s+AS\b", statement, re.IGNORECASE):
        schema.views.add(match.group(1).lower())
    else:
        schema.problems.append(
            f"{filename}: unsupported or destructive statement: {' '.join(statement.split())[:60]!r}"
        )


# ---- checks ---------------------------------------------------------------------------------
def _plain_leading(index_columns: tuple[str, ...]) -> str | None:
    if not index_columns:
        return None
    match = _PLAIN_COLUMN_RE.match(index_columns[0].strip())
    return match.group(1).lower() if match else None


def _unique_sets(schema: Schema, table: Table) -> list[tuple[str, ...]]:
    sets = [table.pk, *table.uniques]
    for index in schema.indexes.values():
        if index.table == table.name and index.unique and index.predicate is None:
            plain = [_PLAIN_COLUMN_RE.match(c.strip()) for c in index.columns]
            if all(plain):
                sets.append(tuple(m.group(1).lower() for m in plain if m))
    return [s for s in sets if s]


def _covered_leading_columns(schema: Schema, table: Table) -> set[str]:
    leading = {s[0] for s in [table.pk, *table.uniques] if s}
    for index in schema.indexes.values():
        if index.table == table.name and index.predicate is None:
            first = _plain_leading(index.columns)
            if first:
                leading.add(first)
    return leading


def _type_key(column: Column) -> tuple[str, bool]:
    return (column.type_name, column.is_array)


def lint_schema(schema: Schema) -> list[str]:
    problems = list(schema.problems)

    for sql_name, python_enum in ENUM_MIRRORS.items():
        sql_values = schema.enums.get(sql_name)
        if sql_values is None:
            problems.append(f"SQL enum {sql_name} is missing")
        elif set(sql_values) != {member.value for member in python_enum}:
            problems.append(
                f"SQL enum {sql_name} differs from {python_enum.__name__}: "
                f"sql-only={sorted(set(sql_values) - {m.value for m in python_enum})} "
                f"python-only={sorted({m.value for m in python_enum} - set(sql_values))}"
            )

    for index in schema.indexes.values():
        if index.table not in schema.tables:
            problems.append(f"index {index.name} is on unknown table {index.table}")
            continue
        for column in index.columns:
            plain = _PLAIN_COLUMN_RE.match(column.strip())
            if plain and plain.group(1).lower() not in schema.tables[index.table].columns:
                problems.append(f"index {index.name} uses unknown column {plain.group(1)}")

    for table in schema.tables.values():
        _lint_table(schema, table, problems)

    for trigger_name, table_name, function in schema.triggers:
        if table_name not in schema.tables:
            problems.append(f"trigger {trigger_name} is on unknown table {table_name}")
        if function not in schema.functions:
            problems.append(f"trigger {trigger_name} calls undefined function {function}")
    return problems


def _lint_table(schema: Schema, table: Table, problems: list[str]) -> None:
    name = table.name
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        problems.append(f"{name}: table names must be lower snake_case")
    if not table.pk:
        problems.append(f"{name}: no primary key")
    for pk_col in table.pk:
        if pk_col not in table.columns:
            problems.append(f"{name}: primary key column {pk_col} does not exist")

    for column in table.columns.values():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", column.name):
            problems.append(f"{name}.{column.name}: column names must be lower snake_case")
        if column.type_name in {"timestamp", "timestamp without time zone"}:
            problems.append(f"{name}.{column.name}: use timestamptz, not timestamp")
        elif column.type_name not in BUILTIN_TYPES and column.type_name not in schema.enums:
            problems.append(f"{name}.{column.name}: unknown type {column.type_name!r}")
        if _SECRET_NAME_RE.search(column.name) and not column.name.endswith("_hash"):
            problems.append(f"{name}.{column.name}: secrets must be stored as hashes (*_hash)")
        if column.type_name in schema.enums and column.default is not None:
            literal = re.fullmatch(r"'([^']*)'(?:::\w+)?", column.default)
            if literal and literal.group(1) not in schema.enums[column.type_name]:
                problems.append(f"{name}.{column.name}: default {literal.group(1)!r} is not a valid {column.type_name}")

    has_created, has_updated = "created_at" in table.columns, "updated_at" in table.columns
    if name in NO_TIMESTAMP_TABLES:
        pass
    elif not has_created:
        problems.append(f"{name}: missing created_at")
    elif not has_updated and name not in APPEND_ONLY_TABLES:
        problems.append(f"{name}: has created_at but no updated_at (append-only tables must be listed)")
    for stamp in ("created_at", "updated_at"):
        stamp_column = table.columns.get(stamp)
        if stamp_column and not (
            stamp_column.type_name == "timestamptz"
            and stamp_column.not_null
            and stamp_column.default is not None
            and stamp_column.default.lower().startswith("now()")
        ):
            problems.append(f"{name}.{stamp}: must be timestamptz NOT NULL DEFAULT now()")
    if has_updated and not any(t_table == name and fn == "set_updated_at" for _, t_table, fn in schema.triggers):
        problems.append(f"{name}: has updated_at but no BEFORE UPDATE trigger calling set_updated_at()")

    covered = _covered_leading_columns(schema, table)
    for fk in table.fks:
        _lint_foreign_key(schema, table, fk, covered, problems)

    if "deleted_at" in table.columns:
        pk = set(table.pk)
        for unique in table.uniques:
            if not pk <= set(unique):
                problems.append(
                    f"{name}: UNIQUE {unique} would block re-creating a soft-deleted row; "
                    "use a partial unique index WHERE deleted_at IS NULL"
                )
        for index in schema.indexes.values():
            if index.table == name and index.unique:
                plain = [_PLAIN_COLUMN_RE.match(c.strip()) for c in index.columns]
                includes_pk = all(plain) and pk <= {m.group(1).lower() for m in plain if m}
                if not includes_pk and not (index.predicate and _SOFT_DELETE_PREDICATE_RE.search(index.predicate)):
                    problems.append(f"{name}: unique index {index.name} must be partial WHERE deleted_at IS NULL")

    for column in table.columns.values():
        values = schema.enums.get(column.type_name)
        if not values:
            continue
        for expression in table.checks:
            for enum_literal in _enum_literals(expression, column.name):
                if enum_literal not in values:
                    problems.append(
                        f"{name}: CHECK compares {column.name} with {enum_literal!r}, "
                        f"which is not a {column.type_name} value"
                    )


def _enum_literals(expression: str, column: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(rf"\b{re.escape(column)}\s*(?:=|<>|!=)\s*'([^']*)'", expression):
        found.append(match.group(1))
    for match in re.finditer(rf"\b{re.escape(column)}\s+(?:NOT\s+)?IN\s*\(([^)]*)\)", expression, re.I):
        found.extend(re.findall(r"'([^']*)'", match.group(1)))
    return found


def _lint_foreign_key(schema: Schema, table: Table, fk: ForeignKey, covered: set[str], problems: list[str]) -> None:
    label = f"{table.name}({', '.join(fk.columns)}) -> {fk.ref_table}"
    if fk.on_delete is None:
        problems.append(f"{label}: state ON DELETE explicitly (CASCADE, RESTRICT or SET NULL)")
    target = schema.tables.get(fk.ref_table)
    if target is None:
        problems.append(f"{label}: referenced table does not exist")
        return
    if target.order > table.order:
        problems.append(f"{label}: referenced table is created later than the referencing table")
    ref_columns = fk.ref_columns if fk.ref_columns is not None else target.pk
    if len(fk.columns) != len(ref_columns):
        problems.append(f"{label}: column count mismatch")
        return
    missing = [c for c in fk.columns if c not in table.columns]
    missing_ref = [c for c in ref_columns if c not in target.columns]
    if missing or missing_ref:
        problems.append(f"{label}: unknown columns {missing + missing_ref}")
        return
    for local, remote in zip(fk.columns, ref_columns, strict=True):
        if _type_key(table.columns[local]) != _type_key(target.columns[remote]):
            problems.append(f"{label}: type mismatch {local} vs {remote}")
    if set(ref_columns) not in [set(s) for s in _unique_sets(schema, target)]:
        problems.append(f"{label}: referenced columns {ref_columns} are not a primary key or unique")
    if fk.columns[0] not in covered:
        problems.append(f"{label}: foreign key is not covered by a non-partial index")


def build_schema(migrations: list[tuple[str, str]]) -> Schema:
    """Parse ``(filename, sql)`` pairs, in order, into a ``Schema``."""
    schema = Schema()
    for filename, sql in migrations:
        parse_migration(schema, filename, sql)
    return schema


def lint_migrations(migrations: list[tuple[str, str]]) -> list[str]:
    return lint_schema(build_schema(migrations))


def lint_seed_sql(schema: Schema, sql: str) -> list[str]:
    """Check that seed INSERTs name real tables and columns and tolerate re-runs."""
    problems: list[str] = []
    try:
        statements = split_sql_statements(sql)
    except SqlSyntaxError as exc:
        return [f"seed: {exc}"]
    for statement in statements:
        if statement.upper() in {"BEGIN", "COMMIT"}:
            continue
        match = re.match(r"INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\b", statement, re.IGNORECASE)
        if not match:
            problems.append(f"seed: unsupported statement {statement[:50]!r}")
            continue
        table = schema.tables.get(match.group(1).lower())
        if table is None:
            problems.append(f"seed: unknown table {match.group(1)}")
            continue
        for column in _names(match.group(2)):
            if column not in table.columns:
                problems.append(f"seed: {table.name} has no column {column}")
        if not re.search(r"ON\s+CONFLICT\b", statement, re.IGNORECASE):
            problems.append(f"seed: INSERT INTO {table.name} is not re-runnable (no ON CONFLICT)")
    return problems
