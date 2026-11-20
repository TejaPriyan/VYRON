import unittest
from datetime import date

from vyron_api.adapters.memory_graph import InMemoryGraph
from vyron_api.adapters.memory_timeline import InMemoryTimeline
from vyron_api.api import limits
from vyron_api.domain.entities import Entity, EntityKind, Relationship, RelationType
from vyron_api.domain.enums import DatePrecision
from vyron_api.domain.provenance import Provenance
from vyron_api.domain.rules import (
    ENDPOINT_RULES,
    InvalidRelationshipError,
    check_relationship_endpoints,
)
from vyron_api.domain.timeline import TimelineEvent
from vyron_api.providers.graph import (
    MAX_CLUSTER_MEMBERS,
    EntityNotFoundError,
    EntityRepository,
    GraphRepository,
)
from vyron_api.providers.timeline import TimelinePage
from vyron_api.seed.dev_seed import (
    SEED_NOTICE,
    build_seed_graph,
    entity_id,
    seed_entities,
    seed_relationships,
    seed_timeline,
)

K = EntityKind
R = RelationType


def ent(slug: str, kind: EntityKind = K.TECHNOLOGY, name: str | None = None) -> Entity:
    return Entity(f"id-{slug}", kind, slug, name or slug.title(), Provenance.SEED)


def rel(a: str, rel_type: RelationType, b: str) -> Relationship:
    return Relationship(f"id-{a}", f"id-{b}", rel_type, Provenance.SEED)


class RulesTest(unittest.TestCase):
    def test_every_relation_type_has_a_rule(self) -> None:
        self.assertEqual(set(ENDPOINT_RULES), set(RelationType))

    def test_rule_examples(self) -> None:
        check_relationship_endpoints(R.USES_LANGUAGE, K.REPOSITORY, K.LANGUAGE)
        check_relationship_endpoints(R.ACQUIRED, K.COMPANY, K.COMPANY)
        for args in (
            (R.USES_LANGUAGE, K.TECHNOLOGY, K.LANGUAGE),
            (R.USES_LANGUAGE, K.REPOSITORY, K.FRAMEWORK),
            (R.ACQUIRED, K.REPOSITORY, K.COMPANY),
            (R.OWNED_BY, K.REPOSITORY, K.LANGUAGE),
            (R.SUPPORTS, K.COMPANY, K.TECHNOLOGY),
        ):
            with self.subTest(args=args), self.assertRaises(InvalidRelationshipError):
                check_relationship_endpoints(*args)

    def test_related_to_is_unrestricted(self) -> None:
        for source in EntityKind:
            for target in EntityKind:
                check_relationship_endpoints(R.RELATED_TO, source, target)


class GraphMutationTest(unittest.IsolatedAsyncioTestCase):
    def test_duplicates_and_bad_relationships_are_rejected(self) -> None:
        graph = InMemoryGraph([ent("a"), ent("b")])
        with self.assertRaises(ValueError):
            graph.add_entity(ent("a"))
        with self.assertRaises(ValueError):
            graph.add_entity(Entity("other-id", K.TECHNOLOGY, "a", "A2", Provenance.SEED))
        graph.add_entity(Entity("lang-a", K.LANGUAGE, "a", "A", Provenance.SEED))  # other kind: ok
        graph.add_relationship(rel("a", R.USES, "b"))
        with self.assertRaises(ValueError):
            graph.add_relationship(rel("a", R.USES, "b"))
        graph.add_relationship(rel("a", R.DEPENDS_ON, "b"))  # different type: ok
        with self.assertRaises(ValueError):
            graph.add_relationship(rel("a", R.USES, "missing"))
        with self.assertRaises(InvalidRelationshipError):
            graph.add_relationship(rel("a", R.ACQUIRED, "b"))

    def test_protocol_conformance(self) -> None:
        graph = InMemoryGraph()
        self.assertIsInstance(graph, EntityRepository)
        self.assertIsInstance(graph, GraphRepository)


class SeedGraphTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.graph = build_seed_graph()
        self.python = entity_id(K.LANGUAGE, "python")

    def test_seed_is_valid_and_labelled(self) -> None:
        self.assertIn("seed", SEED_NOTICE.lower())
        self.assertGreaterEqual(len(seed_entities()), 15)
        for entity in seed_entities():
            self.assertIs(entity.provenance, Provenance.SEED)
        for relationship in seed_relationships():
            self.assertIs(relationship.provenance, Provenance.SEED)
        for event in seed_timeline():
            self.assertIs(event.provenance, Provenance.SEED)
        names = {e.name for e in seed_entities()}
        for required in (
            "Python", "JavaScript", "TypeScript", "React", "Next.js", "Node.js", "PostgreSQL",
            "Neo4j", "Docker", "Kubernetes", "PyTorch", "TensorFlow", "CUDA", "GitHub", "Vercel",
        ):  # fmt: skip
            self.assertIn(required, names)

    def test_seed_ids_are_stable_uuids(self) -> None:
        self.assertEqual(entity_id(K.LANGUAGE, "python"), entity_id(K.LANGUAGE, "python"))
        self.assertNotEqual(entity_id(K.LANGUAGE, "python"), entity_id(K.LIBRARY, "python"))
        self.assertEqual(len(self.python), 36)

    def test_every_seed_entity_is_connected(self) -> None:
        for entity in self.graph.entities():
            self.assertGreater(self.graph.degree(entity.id), 0, entity.name)

    def test_seed_timeline_is_past_only_and_ordered(self) -> None:
        events = seed_timeline()
        self.assertEqual([e.occurred_on for e in events], sorted(e.occurred_on for e in events))
        for event in events:
            event.check_labelled_for(date(2026, 10, 5))

    async def test_brief_example_path_python_to_nvidia(self) -> None:
        nvidia = entity_id(K.COMPANY, "nvidia")
        hops = await self.graph.shortest_path(self.python, nvidia, max_depth=6)
        assert hops is not None
        self.assertEqual(len(hops), 3)
        slugs = []
        for hop in hops:
            for entity_ref in (hop.source_id, hop.target_id):
                entity = await self.graph.get(entity_ref)
                assert entity is not None
                slugs.append(entity.slug)
        for expected in ("python", "pytorch", "cuda", "nvidia"):
            self.assertIn(expected, slugs)

    async def test_no_path_within_depth(self) -> None:
        nvidia = entity_id(K.COMPANY, "nvidia")
        self.assertIsNone(await self.graph.shortest_path(self.python, nvidia, max_depth=2))

    async def test_neighbors_depth_filter_and_limit(self) -> None:
        one_hop = await self.graph.neighbors(self.python, depth=1, relation_types=None, limit=50)
        self.assertEqual(
            {e.slug for e in one_hop.entities},
            {
                "python",
                "numpy",
                "pytorch",
                "tensorflow",
                "pandas",
                "flask",
                "fastapi",
                "scikit-learn",
                "python-software-foundation",
                "django",
                "jax",
            },
        )
        self.assertFalse(one_hop.truncated)
        for r in one_hop.relationships:
            self.assertIn(r.source_id, {e.id for e in one_hop.entities})
        only_uses = await self.graph.neighbors(self.python, depth=1, relation_types={R.USES}, limit=50)
        self.assertEqual(
            {e.slug for e in only_uses.entities},
            {"python", "pytorch", "tensorflow", "jax", "pandas", "scikit-learn"},
        )
        capped = await self.graph.neighbors(self.python, depth=3, relation_types=None, limit=3)
        self.assertEqual(len(capped.entities), 3)
        self.assertTrue(capped.truncated)
        self.assertEqual(capped.entities[0].id, self.python)

    async def test_neighbors_errors(self) -> None:
        with self.assertRaises(EntityNotFoundError):
            await self.graph.neighbors("nope", depth=1, relation_types=None, limit=5)
        with self.assertRaises(ValueError):
            await self.graph.neighbors(self.python, depth=-1, relation_types=None, limit=5)
        with self.assertRaises(ValueError):
            await self.graph.neighbors(self.python, depth=1, relation_types=None, limit=0)

    async def test_get_and_get_by_slug(self) -> None:
        by_slug = await self.graph.get_by_slug(K.LANGUAGE, "python")
        assert by_slug is not None
        self.assertEqual(by_slug.id, self.python)
        self.assertIsNone(await self.graph.get_by_slug(K.TECHNOLOGY, "python"))
        self.assertIsNone(await self.graph.get("nope"))

    async def test_search_ranking_aliases_kinds_and_paging(self) -> None:
        page = await self.graph.search("postgres", kinds=None, limit=5, offset=0)
        self.assertEqual(page.items[0].slug, "postgresql")
        page = await self.graph.search("k8s", kinds=None, limit=5, offset=0)
        self.assertEqual([e.slug for e in page.items], ["kubernetes"])
        page = await self.graph.search("pyhton", kinds=None, limit=5, offset=0)
        self.assertEqual(page.items[0].slug, "python")
        page = await self.graph.search("nextjs", kinds=None, limit=5, offset=0)
        self.assertEqual(page.items[0].slug, "next-js")
        page = await self.graph.search("js", kinds={K.LANGUAGE}, limit=5, offset=0)
        self.assertEqual([e.slug for e in page.items], ["javascript"])
        everything = await self.graph.search("o", kinds=None, limit=100, offset=0)
        first = await self.graph.search("o", kinds=None, limit=2, offset=0)
        second = await self.graph.search("o", kinds=None, limit=2, offset=2)
        self.assertEqual(first.total, everything.total)
        self.assertEqual(
            [e.id for e in [*first.items, *second.items]],
            [e.id for e in everything.items[:4]],
        )

    async def test_search_empty_and_invalid(self) -> None:
        page = await self.graph.search("zzzzzz", kinds=None, limit=5, offset=0)
        self.assertEqual((list(page.items), page.total), ([], 0))
        page = await self.graph.search("   ", kinds=None, limit=5, offset=0)
        self.assertEqual(page.total, 0)
        with self.assertRaises(ValueError):
            await self.graph.search("x", kinds=None, limit=0, offset=0)
        with self.assertRaises(ValueError):
            await self.graph.search("x", kinds=None, limit=1, offset=-1)

    def test_pagerank_components_and_clusters(self) -> None:
        ranks = self.graph.pagerank()
        self.assertAlmostEqual(sum(ranks.values()), 1.0, places=9)
        self.assertGreater(ranks[entity_id(K.PLATFORM, "cuda")], ranks[entity_id(K.COMPANY, "nvidia")])
        components = self.graph.components()
        self.assertEqual(sum(len(c) for c in components), self.graph.entity_count)
        clusters = self.graph.clusters()
        self.assertEqual(sum(len(c.member_ids) for c in clusters), self.graph.entity_count)
        self.assertEqual(clusters, self.graph.clusters())  # deterministic
        ai = next(c for c in clusters if entity_id(K.FRAMEWORK, "pytorch") in c.member_ids)
        self.assertIn(entity_id(K.LIBRARY, "transformers"), ai.member_ids)

    async def test_overview_clusters_list_their_members(self) -> None:
        overview = await self.graph.overview(top=3, max_clusters=20)
        listed = [member for cluster in overview.clusters for member in cluster.member_ids]
        self.assertEqual(len(listed), len(set(listed)), "a member belongs to one cluster")
        self.assertEqual({e.id for e in self.graph.entities()}, set(listed))
        for cluster in overview.clusters:
            self.assertEqual(len(cluster.member_ids), cluster.size)
        ranks = self.graph.pagerank()
        for cluster in overview.clusters:
            scores = [ranks[m] for m in cluster.member_ids]
            self.assertEqual(scores, sorted(scores, reverse=True), "most central member first")

    async def test_overview_cuts_a_very_large_cluster_and_keeps_its_size(self) -> None:
        hub = ent("hub")
        spokes = [ent(f"spoke-{i:03d}") for i in range(MAX_CLUSTER_MEMBERS + 5)]
        graph = InMemoryGraph([hub, *spokes], [rel(s.slug, R.RELATED_TO, "hub") for s in spokes])
        (cluster,) = (await graph.overview(top=1, max_clusters=5)).clusters
        self.assertEqual(cluster.size, MAX_CLUSTER_MEMBERS + 6, "the hub plus every spoke")
        self.assertEqual(len(cluster.member_ids), MAX_CLUSTER_MEMBERS)
        self.assertEqual(cluster.member_ids[0], hub.id, "the cut keeps the most central members")

    def test_cluster_member_limit_is_the_same_in_the_contract_and_the_provider(self) -> None:
        self.assertEqual(MAX_CLUSTER_MEMBERS, limits.MAX_CLUSTER_MEMBERS)

    def test_relationships_of(self) -> None:
        rels = self.graph.relationships_of(self.python)
        self.assertEqual(len(rels), 10)
        with self.assertRaises(EntityNotFoundError):
            self.graph.relationships_of("nope")


class TimelineStoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.store = InMemoryTimeline(seed_timeline())

    async def between(
        self,
        start: int | None,
        end: int | None,
        *,
        include_predictions: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> TimelinePage:
        return await self.store.between(start, end, include_predictions=include_predictions, limit=limit, offset=offset)

    async def test_range_filter_and_ordering(self) -> None:
        page = await self.between(2010, 2015)
        self.assertEqual(
            [e.occurred_on.year for e in page.items],
            [2010, 2010, 2012, 2013, 2013, 2014, 2014, 2014, 2015, 2015],
        )
        self.assertEqual(page.total, 10)
        everything = await self.between(None, None)
        self.assertEqual(len(everything.items), len(seed_timeline()))
        with self.assertRaises(ValueError):
            await self.between(2020, 2010)

    async def test_paging_is_stable_and_exhaustive(self) -> None:
        full = [e.id for e in (await self.between(None, None)).items]
        collected: list[str] = []
        for offset in range(0, len(full), 4):
            page = await self.between(None, None, limit=4, offset=offset)
            self.assertEqual(page.total, len(full))
            collected.extend(e.id for e in page.items)
        self.assertEqual(collected, full)
        self.assertEqual((await self.between(None, None, offset=len(full))).items, [])
        with self.assertRaises(ValueError):
            await self.between(None, None, limit=0)
        with self.assertRaises(ValueError):
            await self.between(None, None, offset=-1)

    async def test_predictions_are_opt_in(self) -> None:
        prediction = TimelineEvent(
            "p1", entity_id(K.LANGUAGE, "python"), date(2035, 1, 1), DatePrecision.YEAR,
            "Scenario: something", "scenario", Provenance.PREDICTED, is_prediction=True,
        )  # fmt: skip
        self.store.add(prediction)
        self.assertNotIn("p1", [e.id for e in (await self.between(None, None)).items])
        with_predictions = await self.between(None, None, include_predictions=True)
        self.assertIn("p1", [e.id for e in with_predictions.items])
        with self.assertRaises(ValueError):
            self.store.add(prediction)

    async def test_for_entities(self) -> None:
        python = entity_id(K.LANGUAGE, "python")
        page = await self.store.for_entities({python}, include_predictions=False, limit=10, offset=0)
        self.assertEqual([e.title for e in page.items], ["Python first released"])
        self.assertEqual(page.total, 1)


if __name__ == "__main__":
    unittest.main()
