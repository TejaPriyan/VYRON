"""Split a SQL script into statements, honoring comments, quotes and dollar quoting.

Only what migrations need: ``--`` and nested ``/* */`` comments (dropped), single-quoted strings
(``''`` escape), double-quoted identifiers, and ``$tag$ ... $tag$`` bodies. Escape strings
(``E'...'``) are rejected because their backslash rules differ and no migration should need them.
Every statement must end with ``;``.
"""

from __future__ import annotations

import re

_DOLLAR_TAG_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


class SqlSyntaxError(ValueError):
    """The script cannot be split safely."""


def split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        pair = sql[i : i + 2]
        if pair == "--":
            end = sql.find("\n", i)
            i = n if end == -1 else end
            buffer.append(" ")
        elif pair == "/*":
            depth, i = 1, i + 2
            while i < n and depth:
                if sql[i : i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif sql[i : i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise SqlSyntaxError("unterminated block comment")
            buffer.append(" ")
        elif ch == "'":
            if i > 0 and sql[i - 1] in "eE" and (i < 2 or not sql[i - 2].isalnum()):
                raise SqlSyntaxError("escape strings (E'...') are not supported")
            end = _closing_quote(sql, i, "'")
            buffer.append(sql[i : end + 1])
            i = end + 1
        elif ch == '"':
            end = _closing_quote(sql, i, '"')
            buffer.append(sql[i : end + 1])
            i = end + 1
        elif ch == "$":
            tag = _DOLLAR_TAG_RE.match(sql, i)
            if tag:
                close = sql.find(tag.group(0), tag.end())
                if close == -1:
                    raise SqlSyntaxError(f"unterminated dollar-quoted body {tag.group(0)}")
                end = close + len(tag.group(0))
                buffer.append(sql[i:end])
                i = end
            else:
                buffer.append(ch)
                i += 1
        elif ch == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            i += 1
        else:
            buffer.append(ch)
            i += 1
    if "".join(buffer).strip():
        raise SqlSyntaxError("the last statement is not terminated with ';'")
    return statements


def _closing_quote(sql: str, start: int, quote: str) -> int:
    i = start + 1
    while i < len(sql):
        if sql[i] == quote:
            if sql[i + 1 : i + 2] == quote:
                i += 2
                continue
            return i
        i += 1
    raise SqlSyntaxError(f"unterminated {quote} quote")
