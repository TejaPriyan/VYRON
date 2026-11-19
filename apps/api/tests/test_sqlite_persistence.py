import unittest
from pathlib import Path

from vyron_api.db.seedsql import render_seed_sql
from vyron_api.db.sqlite import SqliteDatabase

ROOT = Path(__file__).resolve().parents[3]
SEED_FILE = ROOT / "infrastructure" / "database" / "seed" / "dev_seed.sql"


class SqlitePersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SqliteDatabase(":memory:")
        self.db.init_schema()

    def tearDown(self) -> None:
        self.db.close()

    def test_init_schema_creates_tables(self) -> None:
        counts = self.db.counts()
        self.assertEqual(counts["entities"], 0)
        self.assertEqual(counts["relationships"], 0)
        self.assertEqual(counts["timeline_events"], 0)

    def test_load_seed_sql_populates_graph(self) -> None:
        seed_sql = render_seed_sql()
        self.db.load_seed_sql(seed_sql)

        counts = self.db.counts()
        self.assertEqual(counts["entities"], 60)
        self.assertEqual(counts["relationships"], 68)
        self.assertEqual(counts["timeline_events"], 41)
        self.assertGreater(counts["entity_aliases"], 20)

        # Ensure seed data triggers zero integrity violations
        violations = self.db.verify_integrity()
        self.assertEqual(violations, [])

    def test_integrity_views_detect_unsupported_sourced_relationships(self) -> None:
        # Insert entity
        self.db.conn.execute(
            "INSERT INTO entities (id, kind, slug, name, provenance) "
            "VALUES ('ent-1', 'library', 'lib-a', 'Lib A', 'sourced'), "
            "       ('ent-2', 'library', 'lib-b', 'Lib B', 'sourced');"
        )
        # Sourced entity without evidence should be flagged
        violations = self.db.verify_integrity()
        self.assertEqual(len(violations), 2)
        self.assertIn("sourced entity without evidence", violations[0])

        # Add source and evidence for ent-1 and ent-2
        self.db.conn.execute(
            "INSERT INTO sources (id, url, title, source_type, reliability, retrieved_at) "
            "VALUES ('src-1', 'https://example.com/src', 'Docs', 'official_docs', 'primary', '2026-10-27T00:00:00Z');"
        )
        self.db.conn.execute(
            "INSERT INTO evidence (id, entity_id, source_id, claim, retrieved_at) "
            "VALUES ('ev-1', 'ent-1', 'src-1', 'claim 1', '2026-10-27T00:00:00Z'), "
            "       ('ev-2', 'ent-2', 'src-1', 'claim 2', '2026-10-27T00:00:00Z');"
        )
        # Now entities have evidence
        self.assertEqual(self.db.verify_integrity(), [])

        # Add a sourced relationship without relationship_evidence
        self.db.conn.execute(
            "INSERT INTO relationships (id, source_entity_id, target_entity_id, relation_type, provenance) "
            "VALUES ('rel-1', 'ent-1', 'ent-2', 'DEPENDS_ON', 'sourced');"
        )
        violations = self.db.verify_integrity()
        self.assertEqual(len(violations), 1)
        self.assertIn("sourced relationship without evidence: rel-1", violations[0])

        # Link relationship to evidence
        self.db.conn.execute(
            "INSERT INTO relationship_evidence (relationship_id, evidence_id) VALUES ('rel-1', 'ev-1');"
        )
        self.assertEqual(self.db.verify_integrity(), [])


if __name__ == "__main__":
    unittest.main()
