"""Operation handlers: parsed parameters in, ``(status, body)`` out. No HTTP details live here."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from vyron_api.domain.entities import EntityKind, RelationType
from vyron_api.services.catalog import COMPANY_KINDS, TECHNOLOGY_KINDS, CatalogService

Params = Mapping[str, Any]
Result = tuple[int, dict[str, Any]]
Handler = Callable[[CatalogService, Params, Params], Awaitable[Result]]


def _kinds(values: list[str] | None) -> list[EntityKind] | None:
    return [EntityKind(v) for v in values] if values else None


async def health_live(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, service.liveness()


async def health_ready(service: CatalogService, path: Params, query: Params) -> Result:
    body = await service.readiness()
    return (200 if body["status"] == "ok" else 503), body


async def get_meta(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, service.meta()


async def list_entities(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.list_entities(
        kinds=_kinds(query["kind"]), limit=query["limit"], cursor=query["cursor"]
    )


async def list_technologies(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.list_entities(
        kinds=_kinds(query["kind"]),
        limit=query["limit"],
        cursor=query["cursor"],
        group=TECHNOLOGY_KINDS,
        operation_id="list_technologies",
    )


async def list_companies(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.list_entities(
        kinds=_kinds(query["kind"]),
        limit=query["limit"],
        cursor=query["cursor"],
        group=COMPANY_KINDS,
        operation_id="list_companies",
    )


async def search(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.search(
        q=query["q"], kinds=_kinds(query["kind"]), limit=query["limit"], cursor=query["cursor"]
    )


async def get_entity(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.get_entity(path["entity_id"])


async def get_entity_by_slug(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.get_entity_by_slug(EntityKind(path["kind"]), path["slug"])


async def get_entity_neighbors(service: CatalogService, path: Params, query: Params) -> Result:
    relation_types = [RelationType(v) for v in query["relation_type"]] if query["relation_type"] else None
    return 200, await service.neighbors(
        path["entity_id"],
        depth=query["depth"],
        relation_types=relation_types,
        max_nodes=query["max_nodes"],
    )


async def get_entity_timeline(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.entity_timeline(
        path["entity_id"],
        include_predictions=query["include_predictions"],
        limit=query["limit"],
        cursor=query["cursor"],
    )


async def graph_overview(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.overview(top=query["top"], clusters=query["clusters"])


async def graph_path(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.path(query["from"], query["to"], max_depth=query["max_depth"])


async def timeline(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.timeline(
        start_year=query["start_year"],
        end_year=query["end_year"],
        include_predictions=query["include_predictions"],
        limit=query["limit"],
        cursor=query["cursor"],
    )


async def github_parse(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, service.parse_github(query["input"])


async def get_repository_analysis(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.repository_analysis(path["owner"], path["repo"])


async def get_repository_activity(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.repository_activity(path["owner"], path["repo"])


async def get_commit_activity(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.commit_activity(path["owner"], path["repo"])


async def get_package_dependencies(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.package_dependencies(path["registry"], query["name"])


async def get_package_releases(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.package_releases(path["registry"], query["name"])


async def get_github_rate_limit(service: CatalogService, path: Params, query: Params) -> Result:
    return 200, await service.github_rate_limit()


HANDLERS: Mapping[str, Handler] = {
    "health_live": health_live,
    "health_ready": health_ready,
    "get_meta": get_meta,
    "list_entities": list_entities,
    "get_entity": get_entity,
    "get_entity_by_slug": get_entity_by_slug,
    "get_entity_neighbors": get_entity_neighbors,
    "get_entity_timeline": get_entity_timeline,
    "list_technologies": list_technologies,
    "list_companies": list_companies,
    "search": search,
    "graph_overview": graph_overview,
    "graph_path": graph_path,
    "timeline": timeline,
    "github_parse": github_parse,
    "get_repository_analysis": get_repository_analysis,
    "get_repository_activity": get_repository_activity,
    "get_commit_activity": get_commit_activity,
    "get_package_dependencies": get_package_dependencies,
    "get_package_releases": get_package_releases,
    "get_github_rate_limit": get_github_rate_limit,
}
