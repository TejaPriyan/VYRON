"""In-memory ``EntityRepository`` + ``GraphRepository``.

Used for tests, the development seed and as the reference for the PostgreSQL/Neo4j adapters, which
must return the same results for the same data. Traversal treats edges as undirected; relationships
keep their stored direction.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from vyron_api.domain.entities import Entity, EntityKind, Relationship, RelationType
from vyron_api.domain.rules import check_relationship_endpoints
from vyron_api.graph import algorithms
from vyron_api.graph.search import score_entity
from vyron_api.providers.graph import (
    MAX_CLUSTER_MEMBERS,
    ClusterSummary,
    EntityNotFoundError,
    EntityPage,
    GraphOverview,
    RankedEntity,
    Subgraph,
)


@dataclass(frozen=True, slots=True)
class Cluster:
    id: str
    member_ids: tuple[str, ...]
    label: str


class InMemoryGraph:
    def __init__(
        self, entities: Iterable[Entity] = (), relationships: Iterable[Relationship] = ()
    ) -> None:
        self._entities: dict[str, Entity] = {}
        self._by_slug: dict[tuple[EntityKind, str], str] = {}
        self._relationships: dict[tuple[str, str, RelationType], Relationship] = {}
        for entity in entities:
            self.add_entity(entity)
        for relationship in relationships:
            self.add_relationship(relationship)

    # ---- mutation -------------------------------------------------------------------------
    def add_entity(self, entity: Entity) -> None:
        if entity.id in self._entities:
            raise ValueError(f"duplicate entity id: {entity.id}")
        slug_key = (entity.kind, entity.slug)
        if slug_key in self._by_slug:
            raise ValueError(f"duplicate {entity.kind.value} slug: {entity.slug}")
        self._entities[entity.id] = entity
        self._by_slug[slug_key] = entity.id

    def add_relationship(self, relationship: Relationship) -> None:
        source = self._entities.get(relationship.source_id)
        target = self._entities.get(relationship.target_id)
        if source is None or target is None:
            raise ValueError("relationship endpoints must be existing entities")
        check_relationship_endpoints(relationship.type, source.kind, target.kind)
        key = (relationship.source_id, relationship.target_id, relationship.type)
        if key in self._relationships:
            raise ValueError(
                f"duplicate relationship: {source.slug} {relationship.type.value} {target.slug}"
            )
        self._relationships[key] = relationship

    # ---- inspection -----------------------------------------------------------------------
    @property
    def entity_count(self) -> int:
        return len(self._entities)

    @property
    def relationship_count(self) -> int:
        return len(self._relationships)

    def find_entity(self, kind: EntityKind, slug: str) -> Entity | None:
        entity_id = self._by_slug.get((kind, slug))
        return self._entities.get(entity_id) if entity_id is not None else None

    def has_relationship(self, source_id: str, target_id: str, type: RelationType) -> bool:  # noqa: A002
        return (source_id, target_id, type) in self._relationships

    def entities(self) -> list[Entity]:
        return sorted(self._entities.values(), key=lambda e: (e.name.casefold(), e.id))

    def relationships(self) -> list[Relationship]:
        return sorted(
            self._relationships.values(),
            key=lambda r: (r.source_id, r.target_id, r.type.value),
        )

    def relationships_of(self, entity_id: str) -> list[Relationship]:
        self._require(entity_id)
        return [r for r in self.relationships() if entity_id in (r.source_id, r.target_id)]

    def degree(self, entity_id: str) -> int:
        return len(self._adjacency()[self._require(entity_id).id])

    # ---- analysis -------------------------------------------------------------------------
    def pagerank(self) -> dict[str, float]:
        out: dict[str, list[str]] = {entity_id: [] for entity_id in self._entities}
        for relationship in self._relationships.values():
            out[relationship.source_id].append(relationship.target_id)
        return algorithms.pagerank(out)

    def components(self) -> list[list[str]]:
        return algorithms.connected_components(self._adjacency())

    def clusters(self) -> list[Cluster]:
        """Graph communities, largest first, labelled by the most central member's name."""
        communities = algorithms.modularity_communities(self._adjacency())
        ranks = self.pagerank()
        groups: dict[str, list[str]] = {}
        for entity_id, community_id in communities.items():
            groups.setdefault(community_id, []).append(entity_id)
        clusters = []
        for community_id, members in groups.items():
            members.sort()
            lead = min(members, key=lambda m: (-round(ranks[m], 12), m))
            clusters.append(Cluster(community_id, tuple(members), self._entities[lead].name))
        clusters.sort(key=lambda c: (-len(c.member_ids), c.id))
        return clusters

    # ---- EntityRepository -----------------------------------------------------------------
    async def get(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    async def list_entities(
        self, *, kinds: Collection[EntityKind] | None, limit: int, offset: int
    ) -> EntityPage:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset must not be negative")
        selected = [e for e in self.entities() if kinds is None or e.kind in kinds]
        return EntityPage(items=selected[offset : offset + limit], total=len(selected))

    async def get_by_slug(self, kind: EntityKind, slug: str) -> Entity | None:
        entity_id = self._by_slug.get((kind, slug))
        return self._entities[entity_id] if entity_id else None

    async def search(
        self, query: str, *, kinds: Collection[EntityKind] | None, limit: int, offset: int
    ) -> EntityPage:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset must not be negative")
        scored = []
        for entity in self._entities.values():
            if kinds is not None and entity.kind not in kinds:
                continue
            score = score_entity(query, entity.name, entity.aliases, entity.slug)
            if score > 0.0:
                scored.append((-score, entity.name.casefold(), entity.id, entity))
        scored.sort(key=lambda row: row[:3])
        page = [row[3] for row in scored[offset : offset + limit]]
        return EntityPage(items=page, total=len(scored))

    # ---- GraphAnalytics -------------------------------------------------------------------
    async def overview(self, *, top: int, max_clusters: int) -> GraphOverview:
        if top < 0 or max_clusters < 0:
            raise ValueError("top and max_clusters must not be negative")
        ranks = self.pagerank()
        ranked = sorted(ranks, key=lambda e: (-round(ranks[e], 12), self._entities[e].name.casefold(), e))
        return GraphOverview(
            entity_count=self.entity_count,
            relationship_count=self.relationship_count,
            component_count=len(self.components()),
            top_entities=[RankedEntity(self._entities[e], ranks[e]) for e in ranked[:top]],
            clusters=[
                ClusterSummary(c.id, c.label, len(c.member_ids), self._central_first(c.member_ids, ranks))
                for c in self.clusters()[:max_clusters]
            ],
        )

    @staticmethod
    def _central_first(member_ids: Iterable[str], ranks: dict[str, float]) -> tuple[str, ...]:
        ordered = sorted(member_ids, key=lambda m: (-round(ranks[m], 12), m))
        return tuple(ordered[:MAX_CLUSTER_MEMBERS])

    # ---- GraphRepository ------------------------------------------------------------------
    async def neighbors(
        self,
        entity_id: str,
        *,
        depth: int,
        relation_types: Collection[RelationType] | None,
        limit: int,
    ) -> Subgraph:
        self._require(entity_id)
        if depth < 0 or limit < 1:
            raise ValueError("depth must not be negative and limit must be positive")
        adjacency = self._adjacency(relation_types)
        depths = algorithms.bfs_depths(adjacency, entity_id, depth)
        ordered = sorted(
            depths,
            key=lambda e: (depths[e], self._entities[e].name.casefold(), e),
        )
        truncated = len(ordered) > limit
        included = set(ordered[:limit])
        entities = [self._entities[e] for e in ordered[:limit]]
        relationships = [
            r
            for r in self.relationships()
            if r.source_id in included
            and r.target_id in included
            and (relation_types is None or r.type in relation_types)
        ]
        return Subgraph(entities=entities, relationships=relationships, truncated=truncated)

    async def shortest_path(
        self, source_id: str, target_id: str, *, max_depth: int
    ) -> Sequence[Relationship] | None:
        self._require(source_id)
        self._require(target_id)
        path = algorithms.shortest_path(self._adjacency(), source_id, target_id, max_depth)
        if path is None:
            return None
        hops: list[Relationship] = []
        for left, right in zip(path, path[1:], strict=False):
            candidates = [
                r
                for r in self._relationships.values()
                if {r.source_id, r.target_id} == {left, right}
            ]
            hops.append(min(candidates, key=lambda r: (r.source_id, r.type.value)))
        return hops

    # ---- internals ------------------------------------------------------------------------
    def _require(self, entity_id: str) -> Entity:
        entity = self._entities.get(entity_id)
        if entity is None:
            raise EntityNotFoundError(entity_id)
        return entity

    def _adjacency(
        self, relation_types: Collection[RelationType] | None = None
    ) -> algorithms.Adjacency:
        edges = [
            (r.source_id, r.target_id)
            for r in self._relationships.values()
            if relation_types is None or r.type in relation_types
        ]
        return algorithms.undirected_adjacency(self._entities, edges)
