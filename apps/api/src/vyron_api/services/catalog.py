"""Read-side application service: entities, search, graph, timeline.

It depends only on the provider protocols, so the same code serves the in-memory seed graph in tests
and the PostgreSQL/Neo4j adapters later. It returns response dicts that match the contract schemas
and raises ``ApiError`` for client mistakes; infrastructure exceptions are mapped at the edge.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from vyron_api.api import limits
from vyron_api.api import serializers as out
from vyron_api.api.cursor import CursorCodec, InvalidCursorError
from vyron_api.api.errors import ApiError, ErrorCode, FieldIssue, not_found, validation_error
from vyron_api.domain.entities import Entity, EntityKind, Relationship, RelationType
from vyron_api.domain.provenance import SEED_NOTICE, Provenance
from vyron_api.github.url import InvalidRepoReferenceError, parse_github_target, repo_ref_from_parts
from vyron_api.providers.github import GitHubProvider
from vyron_api.providers.graph import (
    EntityNotFoundError,
    EntityRepository,
    GraphAnalytics,
    GraphRepository,
    GraphUnavailableError,
    Subgraph,
)
from vyron_api.providers.registry import PackageProvider, PackageRegistry
from vyron_api.providers.timeline import TimelineRepository

DATA_MODES = ("seed", "database")

TECHNOLOGY_KINDS = frozenset(
    {
        EntityKind.TECHNOLOGY, EntityKind.LANGUAGE, EntityKind.FRAMEWORK, EntityKind.LIBRARY,
        EntityKind.AI_MODEL, EntityKind.DATABASE, EntityKind.PLATFORM, EntityKind.API,
    }
)  # fmt: skip
COMPANY_KINDS = frozenset({EntityKind.COMPANY, EntityKind.ORGANIZATION})


class CatalogService:
    def __init__(
        self,
        entities: EntityRepository,
        graph: GraphRepository,
        timeline: TimelineRepository,
        cursors: CursorCodec,
        *,
        analytics: GraphAnalytics | None = None,
        github: GitHubProvider | None = None,
        packages: PackageProvider | None = None,
        data_mode: str = "database",
    ) -> None:
        if data_mode not in DATA_MODES:
            raise ValueError(f"data_mode must be one of {DATA_MODES}")
        self._entities = entities
        self._graph = graph
        self._timeline = timeline
        self._analytics = analytics
        self._github = github
        self._packages = packages
        self._cursors = cursors
        self._data_mode = data_mode

    # ---- metadata and health ------------------------------------------------------------------
    def meta(self) -> dict[str, Any]:
        return {
            "name": "VYRON",
            "api_version": limits.API_VERSION,
            "data_mode": self._data_mode,
            "data_notice": SEED_NOTICE if self._data_mode == "seed" else None,
            "enums": {
                "entity_kinds": [k.value for k in EntityKind],
                "relation_types": [r.value for r in RelationType],
                "provenance": [p.value for p in Provenance],
            },
            "limits": {
                "max_page_size": limits.MAX_PAGE_SIZE,
                "default_page_size": limits.DEFAULT_PAGE_SIZE,
                "max_graph_depth": limits.MAX_GRAPH_DEPTH,
                "max_graph_nodes": limits.MAX_GRAPH_NODES,
                "max_path_depth": limits.MAX_PATH_DEPTH,
            },
        }

    async def readiness(self) -> dict[str, Any]:
        checks: dict[str, str] = {}
        try:
            await self._entities.list_entities(kinds=None, limit=1, offset=0)
            checks["graph"] = "ok"
        except GraphUnavailableError:
            checks["graph"] = "unavailable"
        checks["analytics"] = "ok" if self._analytics is not None else "not_configured"
        degraded = checks["graph"] != "ok"
        return {"status": "degraded" if degraded else "ok", "checks": checks}

    @staticmethod
    def liveness() -> dict[str, Any]:
        return {"status": "ok", "checks": {}}

    # ---- entities -----------------------------------------------------------------------------
    async def list_entities(
        self,
        *,
        kinds: Collection[EntityKind] | None,
        limit: int,
        cursor: str | None,
        group: frozenset[EntityKind] | None = None,
        operation_id: str = "list_entities",
    ) -> dict[str, Any]:
        effective = self._restrict_kinds(kinds, group)
        filters = {"kind": sorted(k.value for k in effective) if effective else None}
        offset = self._offset(cursor, operation_id, filters)
        page = await self._entities.list_entities(kinds=effective, limit=limit, offset=offset)
        return out.entity_list(
            page.items,
            limit=limit,
            total=page.total,
            next_cursor=self._next_cursor(operation_id, filters, offset, limit, page.total),
        )

    async def search(
        self, *, q: str, kinds: Collection[EntityKind] | None, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        query = q.strip()
        if not query:
            raise validation_error([FieldIssue("q", "must contain visible characters")])
        filters = {"q": query, "kind": sorted(k.value for k in kinds) if kinds else None}
        offset = self._offset(cursor, "search", filters)
        page = await self._entities.search(query, kinds=kinds or None, limit=limit, offset=offset)
        return out.entity_list(
            page.items,
            limit=limit,
            total=page.total,
            next_cursor=self._next_cursor("search", filters, offset, limit, page.total),
        )

    async def get_entity(self, entity_id: str) -> dict[str, Any]:
        found = await self._entities.get(entity_id)
        if found is None:
            raise not_found("entity")
        return out.entity(found)

    async def get_entity_by_slug(self, kind: EntityKind, slug: str) -> dict[str, Any]:
        found = await self._entities.get_by_slug(kind, slug)
        if found is None:
            raise not_found("entity")
        return out.entity(found)

    # ---- graph --------------------------------------------------------------------------------
    async def neighbors(
        self,
        entity_id: str,
        *,
        depth: int,
        relation_types: Collection[RelationType] | None,
        max_nodes: int,
    ) -> dict[str, Any]:
        await self._require(entity_id)  # do not depend on every adapter raising for unknown ids
        graph = await self._graph.neighbors(
            entity_id, depth=depth, relation_types=relation_types or None, limit=max_nodes
        )
        if len(graph.relationships) > limits.MAX_GRAPH_EDGES:  # a dense graph must not blow the payload
            graph = Subgraph(
                entities=graph.entities,
                relationships=list(graph.relationships)[: limits.MAX_GRAPH_EDGES],
                truncated=True,
            )
        return out.subgraph(entity_id, depth, graph)

    async def path(self, source_id: str, target_id: str, *, max_depth: int) -> dict[str, Any]:
        if source_id == target_id:
            raise validation_error([FieldIssue("to", "must differ from 'from'")])
        source = await self._require(source_id)
        target = await self._require(target_id)
        hops = await self._graph.shortest_path(source_id, target_id, max_depth=max_depth)
        if hops is None:
            return out.path_result(source_id, target_id, [], [], found=False)
        hop_list = list(hops)
        nodes = await self._nodes_along(source, hop_list)
        if not nodes or nodes[-1].id != target.id:  # the adapter returned an inconsistent path
            raise ApiError(ErrorCode.INTERNAL_ERROR, "an unexpected error occurred")
        return out.path_result(source_id, target_id, nodes, hop_list, found=True)

    async def overview(self, *, top: int, clusters: int) -> dict[str, Any]:
        if self._analytics is None:
            raise ApiError(ErrorCode.SERVICE_UNAVAILABLE, "graph analytics are not available")
        result = await self._analytics.overview(top=top, max_clusters=clusters)
        extra = [Provenance.SEED] if self._data_mode == "seed" else []
        return out.graph_overview(result, extra)

    # ---- timeline -----------------------------------------------------------------------------
    async def entity_timeline(
        self, entity_id: str, *, include_predictions: bool, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        await self._require(entity_id)
        operation_id = "get_entity_timeline"
        filters = {"entity_id": entity_id, "include_predictions": include_predictions}
        offset = self._offset(cursor, operation_id, filters)
        page = await self._timeline.for_entities(
            [entity_id], include_predictions=include_predictions, limit=limit, offset=offset
        )
        return out.timeline_list(
            list(page.items),
            include_predictions=include_predictions,
            limit=limit,
            total=page.total,
            next_cursor=self._next_cursor(operation_id, filters, offset, limit, page.total),
        )

    async def timeline(
        self,
        *,
        start_year: int | None,
        end_year: int | None,
        include_predictions: bool,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        if start_year is not None and end_year is not None and start_year > end_year:
            raise validation_error([FieldIssue("start_year", "must not be after end_year")])
        filters = {
            "start_year": start_year,
            "end_year": end_year,
            "include_predictions": include_predictions,
        }
        offset = self._offset(cursor, "timeline", filters)
        page = await self._timeline.between(
            start_year, end_year, include_predictions=include_predictions, limit=limit, offset=offset
        )
        return out.timeline_list(
            list(page.items),
            include_predictions=include_predictions,
            limit=limit,
            total=page.total,
            next_cursor=self._next_cursor("timeline", filters, offset, limit, page.total),
        )

    # ---- github (pure) ------------------------------------------------------------------------
    @staticmethod
    def parse_github(raw: str) -> dict[str, Any]:
        return out.github_target(parse_github_target(raw))  # raises InvalidRepoReferenceError

    # ---- live GitHub data ---------------------------------------------------------------------
    def _require_github(self) -> GitHubProvider:
        if self._github is None:
            raise ApiError(ErrorCode.SERVICE_UNAVAILABLE, "GitHub access is switched off on this server")
        return self._github

    async def repository_analysis(self, owner: str, repo: str) -> dict[str, Any]:
        """Read one public repository from GitHub now. ``owner`` and ``repo`` are validated as two names."""
        github = self._require_github()
        try:
            ref = repo_ref_from_parts(owner, repo)
        except InvalidRepoReferenceError as exc:
            raise validation_error([FieldIssue("repo", str(exc))], "not a valid GitHub repository") from exc
        return out.repository_analysis(await github.analyze_repository(ref))

    async def repository_activity(self, owner: str, repo: str) -> dict[str, Any]:
        """Top contributors and the latest release of one public repository (the opt-in second read)."""
        github = self._require_github()
        try:
            ref = repo_ref_from_parts(owner, repo)
        except InvalidRepoReferenceError as exc:
            raise validation_error([FieldIssue("repo", str(exc))], "not a valid GitHub repository") from exc
        return out.repository_activity(await github.repository_activity(ref))

    async def commit_activity(self, owner: str, repo: str) -> dict[str, Any]:
        """A year of weekly commit counts for one public repository (an opt-in read GitHub computes slowly)."""
        github = self._require_github()
        try:
            ref = repo_ref_from_parts(owner, repo)
        except InvalidRepoReferenceError as exc:
            raise validation_error([FieldIssue("repo", str(exc))], "not a valid GitHub repository") from exc
        return out.commit_activity(await github.commit_activity(ref))

    async def package_dependencies(self, registry: str, name: str) -> dict[str, Any]:
        """One package's declared dependencies from its registry (one request; the name is checked first)."""
        if self._packages is None:
            raise ApiError(ErrorCode.SERVICE_UNAVAILABLE, "package registry access is switched off on this server")
        return out.package_report(await self._packages.read_package(PackageRegistry(registry), name))

    async def package_releases(self, registry: str, name: str) -> dict[str, Any]:
        """One package's release dates from its registry (the same document as its dependencies; one request)."""
        if self._packages is None:
            raise ApiError(ErrorCode.SERVICE_UNAVAILABLE, "package registry access is switched off on this server")
        return out.package_releases(await self._packages.read_releases(PackageRegistry(registry), name))

    async def github_rate_limit(self) -> dict[str, Any]:
        return out.rate_limit_status(await self._require_github().rate_limit_status())

    # ---- helpers ------------------------------------------------------------------------------
    @staticmethod
    def _restrict_kinds(
        kinds: Collection[EntityKind] | None, group: frozenset[EntityKind] | None
    ) -> frozenset[EntityKind] | None:
        requested = frozenset(kinds) if kinds else None
        if group is None:
            return requested
        if requested is None:
            return group
        outside = requested - group
        if outside:
            names = ", ".join(sorted(k.value for k in outside))
            raise validation_error([FieldIssue("kind", f"not valid for this collection: {names}")])
        return requested

    def _offset(self, cursor: str | None, operation_id: str, filters: Mapping[str, Any]) -> int:
        if cursor is None:
            return 0
        try:
            return self._cursors.decode(cursor, operation_id, filters)
        except InvalidCursorError as exc:
            raise validation_error([FieldIssue("cursor", str(exc))], "invalid cursor") from exc

    def _next_cursor(
        self, operation_id: str, filters: Mapping[str, Any], offset: int, limit: int, total: int
    ) -> str | None:
        following = offset + limit
        if following >= total or following > 1_000_000:
            return None
        return self._cursors.encode(operation_id, filters, following)

    async def _require(self, entity_id: str) -> Entity:
        found = await self._entities.get(entity_id)
        if found is None:
            raise EntityNotFoundError(entity_id)
        return found

    async def _nodes_along(self, start: Entity, hops: list[Relationship]) -> list[Entity]:
        nodes = [start]
        current = start.id
        for hop in hops:
            if current not in (hop.source_id, hop.target_id):
                return []
            current = hop.target_id if hop.source_id == current else hop.source_id
            nxt = await self._entities.get(current)
            if nxt is None:
                return []
            nodes.append(nxt)
        return nodes
