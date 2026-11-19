import unittest

from vyron_api.domain.entities import Entity, EntityKind, Relationship, RelationType
from vyron_api.domain.provenance import Provenance


class ProvenanceTest(unittest.TestCase):
    def test_only_sourced_requires_evidence(self) -> None:
        required = [p for p in Provenance if p.requires_evidence]
        self.assertEqual(required, [Provenance.SOURCED])

    def test_values_are_stable_strings(self) -> None:
        self.assertEqual(Provenance.AI_GENERATED.value, "ai_generated")
        self.assertEqual(Provenance("seed"), Provenance.SEED)


class EntityTest(unittest.TestCase):
    def make(self, **overrides: object) -> Entity:
        fields: dict[str, object] = {
            "id": "tech:python",
            "kind": EntityKind.LANGUAGE,
            "slug": "python",
            "name": "Python",
            "provenance": Provenance.SEED,
        }
        fields.update(overrides)
        return Entity(**fields)  # type: ignore[arg-type]

    def test_valid_entity(self) -> None:
        self.assertEqual(self.make().slug, "python")

    def test_slug_rules(self) -> None:
        for slug in ("next-js", "node-js", "cuda", "gpt-4"):
            with self.subTest(slug=slug):
                self.assertEqual(self.make(slug=slug).slug, slug)
        for slug in ("", "Python", "next js", "-x", "x-", "a--b", "a_b", "café"):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                self.make(slug=slug)

    def test_blank_id_and_name_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.make(id="  ")
        with self.assertRaises(ValueError):
            self.make(name="")


class RelationshipTest(unittest.TestCase):
    def test_sourced_relationship_needs_evidence(self) -> None:
        with self.assertRaises(ValueError):
            Relationship("a", "b", RelationType.DEPENDS_ON, Provenance.SOURCED)
        rel = Relationship("a", "b", RelationType.DEPENDS_ON, Provenance.SOURCED, ("ev-1",))
        self.assertEqual(rel.evidence_ids, ("ev-1",))

    def test_labelled_relationships_do_not_need_evidence(self) -> None:
        for provenance in (
            Provenance.SEED,
            Provenance.INFERRED,
            Provenance.ESTIMATED,
            Provenance.PREDICTED,
            Provenance.AI_GENERATED,
            Provenance.USER_GENERATED,
        ):
            with self.subTest(provenance=provenance):
                Relationship("a", "b", RelationType.RELATED_TO, provenance)

    def test_self_relationship_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Relationship("a", "a", RelationType.RELATED_TO, Provenance.SEED)

    def test_neo4j_relationship_vocabulary_is_complete(self) -> None:
        expected = {
            "USES", "DEPENDS_ON", "BUILT_ON", "RELATED_TO", "EVOLVED_FROM", "REPLACED_BY",
            "USED_BY", "CREATED_BY", "USES_LANGUAGE", "USES_FRAMEWORK", "OWNED_BY", "FORKS",
            "BUILDS", "OWNS", "ACQUIRED", "INVESTS_IN", "REFERENCES", "RELATES_TO", "SUPPORTS",
            "CONTRADICTS",
        }  # fmt: skip
        self.assertEqual({r.value for r in RelationType}, expected)


if __name__ == "__main__":
    unittest.main()
