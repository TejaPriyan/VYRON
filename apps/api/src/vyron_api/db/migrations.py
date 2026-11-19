"""Migration discovery, planning and execution.

The planner is pure; execution goes through an ``Executor`` so the same logic runs against any
database. The PostgreSQL executor (UNVERIFIED: it needs a driver that is not installable here) must:
take a session-level advisory lock in ``lock()``, run each migration's SQL and the history insert in
one transaction in ``apply()``, and create ``HISTORY_TABLE_DDL`` in ``ensure_history_table()``.
Migrations are forward-only; a mistake is fixed by a new migration, never by editing an applied one
(the checksum check refuses that).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from vyron_api.db.sqlsplit import SqlSyntaxError, split_sql_statements

_FILE_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

HISTORY_TABLE_DDL = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    name text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)"""


class MigrationError(Exception):
    """A migration set is inconsistent or a migration failed."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    sql: str
    checksum: str


def checksum_of(sql: str) -> str:
    """SHA-256 of the script with newlines normalized, so CRLF checkouts do not look modified."""
    return hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def load_migrations(directory: Path) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".") or not path.is_file():
            continue
        match = _FILE_RE.match(path.name)
        if not match:
            raise MigrationError(f"bad migration file name: {path.name} (expected NNNN_name.sql)")
        sql = path.read_text(encoding="utf-8")
        try:
            if not split_sql_statements(sql):
                raise MigrationError(f"{path.name} contains no statements")
        except SqlSyntaxError as exc:
            raise MigrationError(f"{path.name}: {exc}") from exc
        migrations.append(Migration(match["version"], match["name"], sql, checksum_of(sql)))
    versions = [m.version for m in migrations]
    expected = [f"{i:04d}" for i in range(1, len(migrations) + 1)]
    if versions != expected:
        raise MigrationError(f"migration versions must be contiguous from 0001, got {versions}")
    return migrations


def plan(available: Sequence[Migration], applied: Mapping[str, str]) -> list[Migration]:
    """The migrations still to run, refusing any inconsistency with what is already applied."""
    by_version = {m.version: m for m in available}
    for version, recorded in applied.items():
        if version not in by_version:
            raise MigrationError(f"applied migration {version} is missing from the migrations directory")
        if by_version[version].checksum != recorded:
            raise MigrationError(f"migration {version} was modified after it was applied")
    pending = [m for m in available if m.version not in applied]
    if applied and pending and pending[0].version < max(applied):
        raise MigrationError(
            f"migration {pending[0].version} is older than the newest applied migration {max(applied)}"
        )
    return pending


class Executor(Protocol):
    def lock(self) -> AbstractContextManager[None]: ...

    def ensure_history_table(self) -> None: ...

    def applied(self) -> dict[str, str]: ...

    def apply(self, migration: Migration) -> None:
        """Run the migration and record it atomically; raise ``MigrationError`` on failure."""


def migrate(executor: Executor, migrations: Sequence[Migration], *, dry_run: bool = False) -> list[str]:
    """Apply pending migrations in order. Returns the versions that were (or would be) applied."""
    with executor.lock():
        executor.ensure_history_table()
        pending = plan(migrations, executor.applied())
        if not dry_run:
            for migration in pending:
                executor.apply(migration)
        return [m.version for m in pending]


def versions(migrations: Sequence[Migration]) -> Iterator[str]:
    yield from (m.version for m in migrations)
