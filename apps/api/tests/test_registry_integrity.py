"""What registry ingestion leaves in a graph must satisfy VYRON's own trust rules.

The rules are the integrity views of the SQLite adapter: a ``sourced`` entity has evidence of its own, and a
``sourced`` relationship has evidence. Ingestion itself writes nothing to a database; these tests copy the
in-memory result into SQLite only to run the views over it. Fixtures are hand-written (see registry_fakes).
"""

import unittest
from datetime import UTC, datetime

from vyron_api.adapters.memory_graph import InMemoryGraph
from vyron_api.db.sqlite import SqliteDatabase
from vyron_api.domain.entities import EntityKind
from vyron_api.domain.provenance import Provenance
from vyron_api.registries.common import registry_relationship_id
from vyron_api.registries.npm import NpmPackage
from vyron_api.registries.pypi import PyPIPackage
from vyron_api.registries.service import RegistryIngestService
from vyron_api.seed.dev_seed import build_seed_graph

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)


def flask_package() -> PyPIPackage:
    return PyPIPackage(
        name="Flask", slug="flask", version="3.1.3", summary="A web framework.", license_spdx=None, author=None,
        homepage=None, dependencies=("blinker", "click", "werkzeug"), source_url="https://pypi.org/pypi/flask/json",
        conditional_dependencies=("importlib-metadata",),
    )  # fmt: skip


def express_package() -> NpmPackage:
    return NpmPackage(
        name="express", slug="express", version="5.1.0", description="", license_spdx=None, homepage=None,
        dependencies=("accepts", "debug"), source_url="https://registry.npmjs.org/express",
    )  # fmt: skip


def copy_into_sqlite(graph: InMemoryGraph, service: RegistryIngestService) -> SqliteDatabase:
    """Copy the graph and the service's sources and evidence into an in-memory SQLite, unchanged."""
    db = SqliteDatabase(":memory:")
    db.init_schema()
    sql = db.conn
    for e in graph.entities():
        sql.execute(
            "INSERT INTO entities (id, kind, slug, name, provenance) VALUES (?, ?, ?, ?, ?)",
            (e.id, e.kind.value, e.slug, e.name, e.provenance.value),
        )
    for s in service.sources.values():
        sql.execute(
            "INSERT INTO sources (id, url, title, publisher, source_type, reliability, retrieved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (s.id, s.url, s.title, s.publisher, s.source_type.value, s.reliability.value, s.retrieved_at.isoformat()),
        )
    for ev in service.evidence.values():
        sql.execute(
            "INSERT INTO evidence (id, entity_id, source_id, claim, locator, retrieved_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ev.id, ev.entity_id, ev.source_id, ev.claim, ev.locator, ev.retrieved_at.isoformat()),
        )
    for r in graph.relationships():
        rid = registry_relationship_id(r.source_id, r.type, r.target_id)
        sql.execute(
            "INSERT INTO relationships (id, source_entity_id, target_entity_id, relation_type, provenance) "
            "VALUES (?, ?, ?, ?, ?)",
            (rid, r.source_id, r.target_id, r.type.value, r.provenance.value),
        )
        for evidence_id in r.evidence_ids:
            sql.execute(
                "INSERT INTO relationship_evidence (relationship_id, evidence_id) VALUES (?, ?)", (rid, evidence_id)
            )
    return db


class IntoAnEmptyGraph(unittest.TestCase):
    def test_every_sourced_entity_and_relationship_has_evidence(self) -> None:
        service = RegistryIngestService()
        service.ingest_pypi("flask", kind=EntityKind.FRAMEWORK, pkg=flask_package(), retrieved_at=NOW)
        service.ingest_npm("express", kind=EntityKind.FRAMEWORK, pkg=express_package(), retrieved_at=NOW)
        db = copy_into_sqlite(service.graph, service)
        self.addCleanup(db.close)
        self.assertEqual(db.verify_integrity(), [])
        counts = db.counts()
        self.assertEqual((counts["entities"], counts["relationships"]), (7, 5))
        self.assertEqual(counts["evidence"], 7)  # one per entity, since each entity is sourced
        self.assertEqual(counts["sources"], 2)

    def test_the_views_would_have_caught_the_old_behaviour(self) -> None:
        service = RegistryIngestService()
        service.ingest_pypi("flask", pkg=flask_package(), retrieved_at=NOW)
        db = copy_into_sqlite(service.graph, service)
        self.addCleanup(db.close)
        dependency_evidence = [ev for ev in service.evidence.values() if ev.entity_id != service.graph.find_entity(
            EntityKind.LIBRARY, "flask").id]  # type: ignore[union-attr]
        self.assertEqual(len(dependency_evidence), 3)
        db.conn.executemany("DELETE FROM evidence WHERE id = ?", [(ev.id,) for ev in dependency_evidence])
        problems = db.verify_integrity()
        self.assertEqual(len(problems), 3)
        self.assertTrue(all(p.startswith("sourced entity without evidence") for p in problems))

    def test_a_dependencys_evidence_names_the_package_that_listed_it_and_where(self) -> None:
        service = RegistryIngestService()
        result = service.ingest_pypi("flask", pkg=flask_package(), retrieved_at=NOW)
        click = service.graph.find_entity(EntityKind.LIBRARY, "click")
        assert click is not None
        own = [ev for ev in service.evidence.values() if ev.entity_id == click.id]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0].claim, "click is listed as a dependency of Flask in PyPI: Flask")
        self.assertEqual(own[0].locator, "info.requires_dist")
        self.assertEqual(own[0].source_id, result.source.id)

    def test_conditional_dependencies_are_not_in_the_graph_and_the_claim_says_so(self) -> None:
        service = RegistryIngestService()
        result = service.ingest_pypi("flask", pkg=flask_package(), retrieved_at=NOW)
        self.assertIsNone(service.graph.find_entity(EntityKind.LIBRARY, "importlib-metadata"))
        self.assertIn("1 more apply only under a condition", result.evidence.claim)


class IntoTheSeedGraph(unittest.TestCase):
    """Flask is already in the seed, as a seed entity. Ingestion must attach to it, not beside it."""

    def setUp(self) -> None:
        self.graph = build_seed_graph()
        self.service = RegistryIngestService(self.graph)
        self.seed_flask = self.graph.find_entity(EntityKind.FRAMEWORK, "flask")
        assert self.seed_flask is not None
        self.before = self.graph.entity_count

    def test_the_existing_entity_is_reused_and_keeps_its_seed_label(self) -> None:
        result = self.service.ingest_pypi("flask", kind=EntityKind.FRAMEWORK, pkg=flask_package(), retrieved_at=NOW)
        self.assertEqual(result.main_entity.id, self.seed_flask.id)
        self.assertIs(self.graph.find_entity(EntityKind.FRAMEWORK, "flask"), self.seed_flask)
        self.assertEqual(self.seed_flask.provenance, Provenance.SEED)
        self.assertEqual(self.graph.entity_count, self.before + 3)  # only the three dependencies are new

    def test_the_evidence_is_about_the_entity_that_is_actually_in_the_graph(self) -> None:
        result = self.service.ingest_pypi("flask", kind=EntityKind.FRAMEWORK, pkg=flask_package(), retrieved_at=NOW)
        ids = {e.id for e in self.graph.entities()}
        self.assertEqual(result.evidence.entity_id, self.seed_flask.id)
        for ev in self.service.evidence.values():
            self.assertIn(ev.entity_id, ids, ev.claim)
            self.assertIn(ev.source_id, self.service.sources)
        for rel in result.relationships:
            self.assertEqual(rel.source_id, self.seed_flask.id)
            self.assertEqual(rel.evidence_ids, (result.evidence.id,))
            self.assertIn(result.evidence.id, self.service.evidence)

    def test_the_whole_graph_passes_the_integrity_views(self) -> None:
        self.service.ingest_pypi("flask", kind=EntityKind.FRAMEWORK, pkg=flask_package(), retrieved_at=NOW)
        db = copy_into_sqlite(self.graph, self.service)
        self.addCleanup(db.close)
        self.assertEqual(db.verify_integrity(), [])

    def test_ingesting_twice_changes_nothing(self) -> None:
        self.service.ingest_pypi("flask", kind=EntityKind.FRAMEWORK, pkg=flask_package(), retrieved_at=NOW)
        entities, relationships = self.graph.entity_count, self.graph.relationship_count
        evidence = dict(self.service.evidence)
        again = self.service.ingest_pypi("flask", kind=EntityKind.FRAMEWORK, pkg=flask_package(), retrieved_at=NOW)
        self.assertEqual((again.new_entities_count, again.new_relationships_count), (0, 0))
        self.assertEqual((self.graph.entity_count, self.graph.relationship_count), (entities, relationships))
        self.assertEqual(self.service.evidence, evidence)


if __name__ == "__main__":
    unittest.main()
