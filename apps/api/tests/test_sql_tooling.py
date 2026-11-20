import sqlite3
import tempfile
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from vyron_api.db.lint import Schema, build_schema, lint_migrations, lint_schema, lint_seed_sql
from vyron_api.db.migrations import (
    Migration,
    MigrationError,
    checksum_of,
    load_migrations,
    migrate,
    plan,
)
from vyron_api.db.seedsql import quote, render_seed_sql
from vyron_api.db.sqlsplit import SqlSyntaxError, split_sql_statements

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = ROOT / "infrastructure" / "database" / "migrations"
SEED_FILE = ROOT / "infrastructure" / "database" / "seed" / "dev_seed.sql"


class SplitStatementsTest(unittest.TestCase):
    def test_basic_split_and_comment_removal(self) -> None:
        sql = "-- header\nCREATE TABLE a (id int); /* block */ INSERT INTO a VALUES (1);\n"
        self.assertEqual(
            [" ".join(s.split()) for s in split_sql_statements(sql)],
            ["CREATE TABLE a (id int)", "INSERT INTO a VALUES (1)"],
        )

    def test_semicolons_inside_strings_identifiers_and_dollar_bodies(self) -> None:
        sql = (
            "INSERT INTO t VALUES ('a;b', 'it''s; ok');\n"
            'SELECT "we;ird" FROM t;\n'
            "CREATE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;\n"
            "SELECT $tag$ x; $$ y; $tag$;\n"
            "SELECT $1;\n"
        )
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 5)
        self.assertIn("'it''s; ok'", statements[0])
        self.assertIn("RETURN NEW; END;", statements[2])
        self.assertIn("x; $$ y;", statements[3])

    def test_comment_markers_inside_strings_are_kept(self) -> None:
        statements = split_sql_statements("SELECT '-- not a comment', '/* nor this */';")
        self.assertEqual(statements, ["SELECT '-- not a comment', '/* nor this */'"])

    def test_nested_block_comments(self) -> None:
        self.assertEqual(split_sql_statements("/* a /* b */ c */ SELECT 1;"), ["SELECT 1"])

    def test_errors(self) -> None:
        for sql in (
            "SELECT 1",  # unterminated statement
            "SELECT 'oops;",
            'SELECT "oops;',
            "SELECT $$ oops;",
            "/* oops",
            "SELECT E'a\\'b';",
        ):
            with self.subTest(sql=sql), self.assertRaises(SqlSyntaxError):
                split_sql_statements(sql)

    def test_empty_and_comment_only_scripts(self) -> None:
        self.assertEqual(split_sql_statements(""), [])
        self.assertEqual(split_sql_statements("-- nothing\n"), [])


class RealMigrationsLintTest(unittest.TestCase):
    files: list[tuple[str, str]]
    schema: Schema

    @classmethod
    def setUpClass(cls) -> None:
        cls.files = [(p.name, p.read_text(encoding="utf-8")) for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
        cls.schema = build_schema(cls.files)

    def test_migration_files_load_and_are_contiguous(self) -> None:
        migrations = load_migrations(MIGRATIONS_DIR)
        self.assertEqual([m.version for m in migrations], ["0001", "0002", "0003", "0004", "0005"])

    def test_no_lint_problems(self) -> None:
        self.assertEqual(lint_schema(self.schema), [])

    def test_every_brief_entity_has_a_home(self) -> None:
        tables = set(self.schema.tables)
        for required in (
            "users", "organizations", "technologies", "repositories", "languages", "ai_models",
            "datasets", "research_papers", "timeline_events", "sources", "evidence",
            "relationships", "bookmarks", "saved_maps", "search_history", "ai_research_runs",
            "api_usage", "jobs", "notifications", "entities",
        ):  # fmt: skip
            self.assertIn(required, tables)

    def test_jobs_carry_the_fields_the_brief_requires(self) -> None:
        jobs = self.schema.tables["jobs"].columns
        for column in ("status", "started_at", "finished_at", "source", "entity_id", "error_message", "attempt_count"):
            self.assertIn(column, jobs)

    def test_every_foreign_key_states_its_delete_behaviour(self) -> None:
        foreign_keys = [(t.name, fk) for t in self.schema.tables.values() for fk in t.fks]
        self.assertGreater(len(foreign_keys), 30)  # guards against the parser silently finding none
        for table, fk in foreign_keys:
            self.assertIsNotNone(fk.on_delete, f"{table}{fk.columns}")

    def test_secret_columns_are_hashes(self) -> None:
        for table in self.schema.tables.values():
            for column in table.columns:
                if any(word in column for word in ("password", "token", "secret")):
                    self.assertTrue(column.endswith("_hash"), f"{table.name}.{column}")

    def test_enum_values_match_python_domain(self) -> None:
        self.assertEqual(self.schema.enums["provenance"][-1], "seed")
        self.assertEqual(len(self.schema.enums["relation_type"]), 20)


class LinterCatchesProblemsTest(unittest.TestCase):
    """The linter must fail on broken SQL, or a clean result on the real files means nothing."""

    BASE = """
        CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;
        CREATE TABLE parent (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER parent_set_updated_at BEFORE UPDATE ON parent
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """

    def problems(self, extra: str) -> list[str]:
        problems = lint_migrations([("t.sql", self.BASE + extra)])
        return [p for p in problems if not p.startswith("SQL enum")]  # ignore domain-enum mirrors

    def child(self, columns: str, tail: str = "") -> str:
        return f"""
            CREATE TABLE child (
                id uuid PRIMARY KEY,
                {columns},
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            );
            CREATE TRIGGER child_set_updated_at BEFORE UPDATE ON child
                FOR EACH ROW EXECUTE FUNCTION set_updated_at();
            {tail}
        """

    def assertProblem(self, extra: str, fragment: str) -> None:
        problems = self.problems(extra)
        self.assertTrue(any(fragment in p for p in problems), f"expected {fragment!r} in {problems}")

    def test_clean_child_has_no_problems(self) -> None:
        sql = self.child(
            "parent_id uuid NOT NULL REFERENCES parent (id) ON DELETE CASCADE",
            "CREATE INDEX child_parent_idx ON child (parent_id);",
        )
        self.assertEqual(self.problems(sql), [])

    def test_unindexed_foreign_key(self) -> None:
        self.assertProblem(
            self.child("parent_id uuid NOT NULL REFERENCES parent (id) ON DELETE CASCADE"),
            "not covered by a non-partial index",
        )

    def test_partial_index_does_not_cover_a_foreign_key(self) -> None:
        self.assertProblem(
            self.child(
                "parent_id uuid NOT NULL REFERENCES parent (id) ON DELETE CASCADE",
                "CREATE INDEX child_p ON child (parent_id) WHERE id IS NOT NULL;",
            ),
            "not covered",
        )

    def test_foreign_key_must_state_on_delete(self) -> None:
        self.assertProblem(
            self.child("x uuid REFERENCES parent (id)", "CREATE INDEX i ON child (x);"),
            "state ON DELETE explicitly",
        )
        table_level = self.child(
            "x uuid NOT NULL, FOREIGN KEY (x) REFERENCES parent (id)", "CREATE INDEX i ON child (x);"
        )
        self.assertProblem(table_level, "state ON DELETE explicitly")
        stated = self.child(
            "x uuid NOT NULL, FOREIGN KEY (x) REFERENCES parent (id) ON DELETE SET NULL",
            "CREATE INDEX i ON child (x);",
        )
        self.assertEqual(self.problems(stated), [])

    def test_missing_table_and_column_and_type_mismatch(self) -> None:
        self.assertProblem(
            self.child("x uuid REFERENCES nowhere (id) ON DELETE CASCADE", "CREATE INDEX i ON child (x);"),
            "referenced table does not exist",
        )
        self.assertProblem(
            self.child("x uuid REFERENCES parent (nope) ON DELETE CASCADE", "CREATE INDEX i ON child (x);"),
            "unknown columns",
        )
        self.assertProblem(
            self.child("x bigint REFERENCES parent (id) ON DELETE CASCADE", "CREATE INDEX i ON child (x);"),
            "type mismatch",
        )

    def test_foreign_key_to_a_non_unique_column(self) -> None:
        extra = """
            CREATE TABLE other (id uuid PRIMARY KEY, code text,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now());
            CREATE TRIGGER other_set_updated_at BEFORE UPDATE ON other
                FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """ + self.child("c text REFERENCES other (code) ON DELETE CASCADE", "CREATE INDEX i ON child (c);")
        self.assertProblem(extra, "not a primary key or unique")

    def test_referencing_a_table_created_later(self) -> None:
        sql = """
            CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;
            CREATE TABLE early (id uuid PRIMARY KEY, late_id uuid REFERENCES late (id) ON DELETE CASCADE,
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
            CREATE INDEX early_late_idx ON early (late_id);
            CREATE TABLE late (id uuid PRIMARY KEY,
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
        """
        problems = lint_migrations([("t.sql", sql)])
        self.assertTrue(any("created later" in p for p in problems), problems)

    def test_timestamp_and_trigger_rules(self) -> None:
        self.assertProblem(
            "CREATE TABLE a (id uuid PRIMARY KEY, created_at timestamp NOT NULL DEFAULT now(),"
            " updated_at timestamptz NOT NULL DEFAULT now());",
            "use timestamptz",
        )
        self.assertProblem(
            "CREATE TABLE a (id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),"
            " updated_at timestamptz NOT NULL DEFAULT now());",
            "no BEFORE UPDATE trigger",
        )
        self.assertProblem(
            "CREATE TABLE a (id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now());",
            "no updated_at",
        )
        self.assertProblem("CREATE TABLE a (id uuid PRIMARY KEY, name text);", "missing created_at")
        self.assertProblem(
            "CREATE TABLE a (id uuid PRIMARY KEY, created_at timestamptz DEFAULT now(),"
            " updated_at timestamptz NOT NULL DEFAULT now());",
            "must be timestamptz NOT NULL DEFAULT now()",
        )

    def test_missing_primary_key_unknown_type_and_secrets(self) -> None:
        self.assertProblem(
            "CREATE TABLE a (name text, created_at timestamptz NOT NULL DEFAULT now());", "no primary key"
        )
        self.assertProblem(self.child("x frobnicate"), "unknown type")
        self.assertProblem(self.child("password text"), "must be stored as hashes")
        self.assertProblem(self.child("session_token bytea"), "must be stored as hashes")

    def test_soft_delete_uniqueness(self) -> None:
        soft = (
            "CREATE TABLE s (id uuid PRIMARY KEY, email text, deleted_at timestamptz,"
            " created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()"
        )
        trigger = "CREATE TRIGGER s_set_updated_at BEFORE UPDATE ON s FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
        self.assertProblem(soft + ", UNIQUE (email));" + trigger, "soft-deleted")
        self.assertProblem(soft + "); CREATE UNIQUE INDEX s_email ON s (email);" + trigger, "partial")
        self.assertEqual(
            self.problems(soft + "); CREATE UNIQUE INDEX s_email ON s (email) WHERE deleted_at IS NULL;" + trigger),
            [],
        )

    def test_enum_drift_and_bad_literals(self) -> None:
        sql = "CREATE TYPE mood AS ENUM ('happy', 'sad');" + self.child(
            "m mood NOT NULL DEFAULT 'angry' CHECK (m IN ('happy', 'meh'))"
        )
        problems = self.problems(sql)
        self.assertTrue(any("default 'angry'" in p for p in problems), problems)
        self.assertTrue(any("'meh'" in p for p in problems), problems)
        drift = lint_migrations([("t.sql", "CREATE TYPE provenance AS ENUM ('sourced', 'invented');")])
        self.assertTrue(any("differs from Provenance" in p for p in drift), drift)

    def test_destructive_and_unsupported_statements_are_reported(self) -> None:
        for statement in ("DROP TABLE parent;", "ALTER TABLE parent ADD COLUMN x int;", "DELETE FROM parent;"):
            with self.subTest(statement=statement):
                self.assertProblem(statement, "unsupported or destructive")

    def test_unbalanced_and_unterminated_sql_is_reported(self) -> None:
        self.assertTrue(lint_migrations([("t.sql", "CREATE TABLE a (id uuid")]))
        self.assertTrue(lint_migrations([("t.sql", "CREATE TABLE a (id uuid PRIMARY KEY);\nSELECT 'x")]))

    def test_duplicate_objects(self) -> None:
        self.assertProblem("CREATE TABLE parent (id uuid PRIMARY KEY);", "created twice")
        self.assertProblem("CREATE INDEX i ON parent (id); CREATE INDEX i ON parent (id);", "created twice")

    def test_trigger_on_unknown_function_or_table(self) -> None:
        self.assertProblem(
            "CREATE TRIGGER t BEFORE UPDATE ON parent FOR EACH ROW EXECUTE FUNCTION nope();",
            "undefined function",
        )
        self.assertProblem(
            "CREATE TRIGGER t BEFORE UPDATE ON ghost FOR EACH ROW EXECUTE FUNCTION set_updated_at();",
            "unknown table",
        )


class MigrationPlanTest(unittest.TestCase):
    def mig(self, version: str, sql: str = "SELECT 1;") -> Migration:
        return Migration(version, f"m{version}", sql, checksum_of(sql))

    def test_pending_are_those_not_applied(self) -> None:
        available = [self.mig("0001"), self.mig("0002"), self.mig("0003")]
        applied = {"0001": available[0].checksum}
        self.assertEqual([m.version for m in plan(available, applied)], ["0002", "0003"])
        self.assertEqual(plan(available, {m.version: m.checksum for m in available}), [])

    def test_modified_missing_and_out_of_order_are_refused(self) -> None:
        available = [self.mig("0001"), self.mig("0002")]
        with self.assertRaisesRegex(MigrationError, "modified"):
            plan(available, {"0001": "deadbeef"})
        with self.assertRaisesRegex(MigrationError, "missing"):
            plan(available, {"0009": "x"})
        with self.assertRaisesRegex(MigrationError, "older"):
            plan(available, {"0002": available[1].checksum})

    def test_checksum_ignores_newline_style(self) -> None:
        self.assertEqual(checksum_of("a\r\nb\r\n"), checksum_of("a\nb\n"))
        self.assertNotEqual(checksum_of("a"), checksum_of("b"))


class SqliteExecutor:
    """Executes the runner against sqlite3 to prove the mechanics (not the PostgreSQL DDL)."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self._lock = threading.Lock()

    @contextmanager
    def lock(self) -> Iterator[None]:
        with self._lock:
            yield

    def ensure_history_table(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, name text NOT NULL,"
            " checksum text NOT NULL)"
        )

    def applied(self) -> dict[str, str]:
        return dict(self.conn.execute("SELECT version, checksum FROM schema_migrations"))

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

    def tables(self) -> set[str]:
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {name for (name,) in rows}

    def close(self) -> None:
        self.conn.close()


class MigrateWithExecutorTest(unittest.TestCase):
    def write(self, directory: Path, files: dict[str, str]) -> None:
        for name, sql in files.items():
            (directory / name).write_text(sql, encoding="utf-8")

    def test_applies_in_order_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write(directory, {
                "0001_a.sql": "CREATE TABLE a (id integer PRIMARY KEY);",
                "0002_b.sql": "CREATE TABLE b (id integer PRIMARY KEY); INSERT INTO b VALUES (1);",
            })  # fmt: skip
            migrations = load_migrations(directory)
            executor = SqliteExecutor()
            self.addCleanup(executor.close)
            self.assertEqual(migrate(executor, migrations, dry_run=True), ["0001", "0002"])
            self.assertNotIn("a", executor.tables())
            self.assertEqual(migrate(executor, migrations), ["0001", "0002"])
            self.assertEqual(migrate(executor, migrations), [])
            self.assertLessEqual({"a", "b"}, executor.tables())

    def test_failed_migration_rolls_back_completely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write(directory, {
                "0001_ok.sql": "CREATE TABLE ok (id integer PRIMARY KEY);",
                "0002_bad.sql": "CREATE TABLE half (id integer); INSERT INTO nonexistent VALUES (1);",
            })  # fmt: skip
            executor = SqliteExecutor()
            self.addCleanup(executor.close)
            with self.assertRaisesRegex(MigrationError, "0002"):
                migrate(executor, load_migrations(directory))
            self.assertIn("ok", executor.tables())
            self.assertNotIn("half", executor.tables())  # first statement rolled back with the second
            self.assertEqual(list(executor.applied()), ["0001"])

    def test_drift_after_apply_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write(directory, {"0001_a.sql": "CREATE TABLE a (id integer PRIMARY KEY);"})
            executor = SqliteExecutor()
            self.addCleanup(executor.close)
            migrate(executor, load_migrations(directory))
            self.write(directory, {"0001_a.sql": "CREATE TABLE a (id integer PRIMARY KEY, x integer);"})
            with self.assertRaisesRegex(MigrationError, "modified"):
                migrate(executor, load_migrations(directory))

    def test_loader_rejects_bad_directories(self) -> None:
        cases = {
            "bad name": {"1_a.sql": "SELECT 1;"},
            "gap": {"0001_a.sql": "SELECT 1;", "0003_c.sql": "SELECT 1;"},
            "start": {"0002_b.sql": "SELECT 1;"},
            "empty": {"0001_a.sql": "-- nothing\n"},
            "syntax": {"0001_a.sql": "SELECT 'x"},
            "extension": {"0001_a.txt": "SELECT 1;"},
        }
        for label, files in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                self.write(Path(tmp), files)
                with self.assertRaises(MigrationError):
                    load_migrations(Path(tmp))


class SeedSqlTest(unittest.TestCase):
    def test_committed_seed_file_is_current(self) -> None:
        self.assertTrue(SEED_FILE.exists(), "run scripts/generate_seed_sql.py")
        self.assertEqual(SEED_FILE.read_text(encoding="utf-8"), render_seed_sql())

    def test_seed_sql_matches_the_schema_and_is_labelled(self) -> None:
        schema = build_schema([(p.name, p.read_text(encoding="utf-8")) for p in sorted(MIGRATIONS_DIR.glob("*.sql"))])
        self.assertEqual(lint_seed_sql(schema, render_seed_sql()), [])
        text = render_seed_sql()
        self.assertIn("Development seed data", text)
        self.assertNotIn("'sourced'", text)  # seed rows must never claim to be sourced
        self.assertIn("'seed'", text)

    def test_seed_lint_catches_bad_columns_and_non_rerunnable_inserts(self) -> None:
        schema = build_schema([("0001.sql", "CREATE TABLE t (id uuid PRIMARY KEY, created_at timestamptz);")])
        self.assertTrue(lint_seed_sql(schema, "INSERT INTO t (id, nope) VALUES ('a', 'b') ON CONFLICT DO NOTHING;"))
        self.assertTrue(lint_seed_sql(schema, "INSERT INTO ghost (id) VALUES ('a') ON CONFLICT DO NOTHING;"))
        self.assertTrue(lint_seed_sql(schema, "INSERT INTO t (id) VALUES ('a');"))
        self.assertTrue(lint_seed_sql(schema, "DELETE FROM t;"))

    def test_quote_escapes_and_rejects_nul(self) -> None:
        self.assertEqual(quote("it's"), "'it''s'")
        with self.assertRaises(ValueError):
            quote("a\x00b")


if __name__ == "__main__":
    unittest.main()
