from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from vyron_api.domain.entities import Entity, EntityKind, Relationship, RelationType


class GraphUnavailableError(Exception):
    """The graph backend cannot be reached; callers should fall back to relational data."""


class EntityNotFoundError(LookupError):
    """No entity exists with the requested id."""

    def __init__(self, entity_id: str) -> None:
        super().__init__(f"entity not found: {entity_id}")
        self.entity_id = entity_id


@dataclass(frozen=True, slots=True)
class Subgraph:
    entities: Sequence[Entity]
    relationships: Sequence[Relationship]
    truncated: bool = False


@runtime_checkable
class GraphRepository(Protocol):
    async def neighbors(
        self,
        entity_id: str,
        *,
        depth: int,
        relation_types: Collection[RelationType] | None,
        limit: int,
    ) -> Subgraph: ...

    async def shortest_path(
        self, source_id: str, target_id: str, *, max_depth: int
    ) -> Sequence[Relationship] | None: ...


@dataclass(frozen=True, slots=True)
class EntityPage:
    items: Sequence[Entity]
    total: int


@runtime_checkable
class EntityRepository(Protocol):
    async def get(self, entity_id: str) -> Entity | None: ...

    async def list_entities(self, *, kinds: Collection[EntityKind] | None, limit: int, offset: int) -> EntityPage:
        """Entities ordered by name (case-insensitive), then id, so paging is stable."""

    async def get_by_slug(self, kind: EntityKind, slug: str) -> Entity | None: ...

    async def search(
        self, query: str, *, kinds: Collection[EntityKind] | None, limit: int, offset: int
    ) -> EntityPage: ...


MAX_CLUSTER_MEMBERS = 300  # same ceiling as one neighbourhood: a browser is never sent a whole large cluster


@dataclass(frozen=True, slots=True)
class ClusterSummary:
    id: str
    label: str
    size: int
    #: Most central members first, at most ``MAX_CLUSTER_MEMBERS``. Fewer than ``size`` means it was cut.
    member_ids: Sequence[str]


@dataclass(frozen=True, slots=True)
class RankedEntity:
    entity: Entity
    score: float


@dataclass(frozen=True, slots=True)
class GraphOverview:
    entity_count: int
    relationship_count: int
    component_count: int
    top_entities: Sequence[RankedEntity]
    clusters: Sequence[ClusterSummary]


@runtime_checkable
class GraphAnalytics(Protocol):
    """Whole-graph statistics. ``score`` is PageRank over directed relationships (an estimate)."""

    async def overview(self, *, top: int, max_clusters: int) -> GraphOverview: ...
