"""SQLite persistence adapter for VYRON.

Implements the Executor protocol from migrations.py and provides local relational storage
for entities, relationships, sources, evidence, and timeline events, with automatic schema
creation and integrity checks. Standard library only (sqlite3).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from vyron_api.db.migrations import Migration, MigrationError
from vyron_api.db.sqlsplit import split_sql_statements

SQLITE_SCHEMA_DDL = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    name text NOT NULL,
    checksum text NOT NULL,
    applied_at text NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entities (
    id text PRIMARY KEY,
    kind text NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    summary text,
    provenance text NOT NULL,
    created_at text NOT NULL DEFAULT (datetime('now')),
    updated_at text NOT NULL DEFAULT (datetime('now')),
    deleted_at text,
    UNIQUE (id, kind)
);

CREATE UNIQUE INDEX IF NOT EXISTS entities_kind_slug_key ON entities (kind, slug) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS entities_kind_name_idx ON entities (kind, lower(name));

CREATE TABLE IF NOT EXISTS entity_aliases (
    id text PRIMARY KEY,
    entity_id text NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    alias text NOT NULL,
    created_at text NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS technologies (
    entity_id text PRIMARY KEY REFERENCES entities (id) ON DELETE CASCADE,
    kind text NOT NULL,
    category text,
    first_release_year integer,
    homepage_url text,
    license_spdx text,
    created_at text NOT NULL DEFAULT (datetime('now')),
    updated_at text NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS languages (
    entity_id text PRIMARY KEY REFERENCES entities (id) ON DELETE CASCADE,
    kind text NOT NULL DEFAULT 'language',
    first_appeared_year integer,
    created_at text NOT NULL DEFAULT (datetime('now')),
    updated_at text NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS organizations (
    entity_id text PRIMARY KEY REFERENCES entities (id) ON DELETE CASCADE,
    kind text NOT NULL,
    headquarters text,
    website_url text,
    created_at text NOT NULL DEFAULT (datetime('now')),
    updated_at text NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id text PRIMARY KEY,
    url text NOT NULL,
    title text NOT NULL,
    publisher text,
    published_on text,
    source_type text NOT NULL,
    reliability text NOT NULL DEFAULT 'unverified',
    retrieved_at text NOT NULL,
    created_at text NOT NULL DEFAULT (datetime('now')),
    updated_at text NOT NULL DEFAULT (datetime('now')),
    deleted_at text
);

CREATE UNIQUE INDEX IF NOT EXISTS sources_url_key ON sources (url) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS evidence (
    id text PRIMARY KEY,
    entity_id text NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    source_id text NOT NULL REFERENCES sources (id) ON DELETE RESTRICT,
    claim text NOT NULL,
    locator text,
    retrieved_at text NOT NULL,
    created_at text NOT NULL DEFAULT (datetime('now')),
    updated_at text NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS evidence_entity_idx ON evidence (entity_id);
CREATE INDEX IF NOT EXISTS evidence_source_idx ON evidence (source_id);

CREATE TABLE IF NOT EXISTS relationships (
    id text PRIMARY KEY,
    source_entity_id text NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    target_entity_id text NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    relation_type text NOT NULL,
    provenance text NOT NULL,
    created_at text NOT NULL DEFAULT (datetime('now')),
    updated_at text NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_entity_id, target_entity_id, relation_type)
);

CREATE INDEX IF NOT EXISTS relationships_target_idx ON relationships (target_entity_id, relation_type);

CREATE TABLE IF NOT EXISTS relationship_evidence (
    relationship_id text NOT NULL REFERENCES relationships (id) ON DELETE CASCADE,
    evidence_id text NOT NULL REFERENCES evidence (id) ON DELETE CASCADE,
    PRIMARY KEY (relationship_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS relationship_evidence_evidence_idx ON relationship_evidence (evidence_id);

CREATE TABLE IF NOT EXISTS timeline_events (
    id text PRIMARY KEY,
    entity_id text NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    occurred_on text NOT NULL,
    date_precision text NOT NULL,
    title text NOT NULL,
    event_type text NOT NULL,
    provenance text NOT NULL,
    is_prediction integer NOT NULL DEFAULT 0,
    created_at text NOT NULL DEFAULT (datetime('now')),
    updated_at text NOT NULL DEFAULT (datetime('now'))
);

CREATE VIEW IF NOT EXISTS integrity_sourced_relationships_without_evidence AS
SELECT r.id
FROM relationships r
WHERE r.provenance = 'sourced'
  AND NOT EXISTS (SELECT 1 FROM relationship_evidence re WHERE re.relationship_id = r.id);

CREATE VIEW IF NOT EXISTS integrity_sourced_entities_without_evidence AS
SELECT e.id
FROM entities e
WHERE e.provenance = 'sourced'
  AND e.deleted_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM evidence ev WHERE ev.entity_id = e.id);
"""


class SqliteDatabase:
    """SQLite database adapter implementing Executor and high-level graph persistence."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.path = str(db_path)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self._lock = threading.Lock()

    @contextmanager
    def lock(self) -> Iterator[None]:
        with self._lock:
            yield

    def ensure_history_table(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "    version text PRIMARY KEY,"
            "    name text NOT NULL,"
            "    checksum text NOT NULL,"
            "    applied_at text NOT NULL DEFAULT (datetime('now'))"
            ");"
        )

    def applied(self) -> dict[str, str]:
        self.ensure_history_table()
        rows = self.conn.execute("SELECT version, checksum FROM schema_migrations")
        return dict(rows)

    def apply(self, migration: Migration) -> None:
        self.conn.execute("BEGIN")
        try:
            for statement in split_sql_statements(migration.sql):
                self.conn.execute(statement)
            self.conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
                (migration.version, migration.name, migration.checksum),
            )
            self.conn.execute("COMMIT")
        except sqlite3.Error as exc:
            self.conn.execute("ROLLBACK")
            raise MigrationError(f"migration {migration.version} failed: {exc}") from exc

    def init_schema(self) -> None:
        """Initialize the full SQLite DDL schema."""
        self.conn.executescript(SQLITE_SCHEMA_DDL)

    def load_seed_sql(self, sql_text: str) -> None:
        """Load seed SQL statements (INSERT ... ON CONFLICT DO NOTHING)."""
        self.conn.executescript(sql_text)

    def verify_integrity(self) -> list[str]:
        """Check integrity views and return list of violating ids."""
        violations: list[str] = []
        rows = self.conn.execute("SELECT id FROM integrity_sourced_relationships_without_evidence").fetchall()
        for (rel_id,) in rows:
            violations.append(f"sourced relationship without evidence: {rel_id}")
        rows = self.conn.execute("SELECT id FROM integrity_sourced_entities_without_evidence").fetchall()
        for (ent_id,) in rows:
            violations.append(f"sourced entity without evidence: {ent_id}")
        return violations

    def counts(self) -> dict[str, int]:
        """Return row counts for all core domain tables."""
        tables = [
            "entities",
            "relationships",
            "timeline_events",
            "sources",
            "evidence",
            "relationship_evidence",
            "entity_aliases",
        ]
        res: dict[str, int] = {}
        for table in tables:
            try:
                (count,) = self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()
                res[table] = count
            except sqlite3.OperationalError:
                res[table] = 0
        return res

    def close(self) -> None:
        self.conn.close()
