"""Domain objects to JSON-ready dicts matching the schemas in ``schemas.py``.

Every response that can contain development seed data carries ``data_notice`` so no client can show
seed rows as production facts without ignoring an explicit field. Serializers never invent values:
fields the domain does not have are simply absent from the schema.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from vyron_api.domain.entities import Entity, Relationship
from vyron_api.domain.provenance import SEED_NOTICE, Provenance
from vyron_api.domain.timeline import TimelineEvent
from vyron_api.github.ratelimit import RateLimitState
from vyron_api.github.url import GitHubTarget
from vyron_api.providers.github import CommitActivityResult, RepoActivityResult, RepoAnalysisResult
from vyron_api.providers.graph import GraphOverview, Subgraph
from vyron_api.providers.registry import PackageResult, ReleaseResult

SCORE_DIGITS = 6


def data_notice(provenances: Iterable[Provenance]) -> str | None:
    """The seed-data notice when any provenance is ``seed``, otherwise None."""
    return SEED_NOTICE if any(p is Provenance.SEED for p in provenances) else None


def entity(item: Entity) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind.value,
        "slug": item.slug,
        "name": item.name,
        "aliases": list(item.aliases),
        "provenance": item.provenance.value,
    }


def relationship(item: Relationship) -> dict[str, Any]:
    return {
        "source_id": item.source_id,
        "target_id": item.target_id,
        "type": item.type.value,
        "provenance": item.provenance.value,
        "evidence_ids": list(item.evidence_ids),
    }


def timeline_event(item: TimelineEvent) -> dict[str, Any]:
    return {
        "id": item.id,
        "entity_id": item.entity_id,
        "occurred_on": item.occurred_on.isoformat(),
        "date_precision": item.precision.value,
        "title": item.title,
        "event_type": item.event_type,
        "provenance": item.provenance.value,
        "is_prediction": item.is_prediction,
        "description": item.description,
    }


def entity_list(
    items: Iterable[Entity], *, limit: int, total: int, next_cursor: str | None
) -> dict[str, Any]:
    entities = list(items)
    return {
        "items": [entity(e) for e in entities],
        "page": {"limit": limit, "total": total, "next_cursor": next_cursor},
        "data_notice": data_notice(e.provenance for e in entities),
    }


def subgraph(center_id: str, depth: int, graph: Subgraph) -> dict[str, Any]:
    provenances = [e.provenance for e in graph.entities] + [r.provenance for r in graph.relationships]
    return {
        "center_id": center_id,
        "depth": depth,
        "entities": [entity(e) for e in graph.entities],
        "relationships": [relationship(r) for r in graph.relationships],
        "truncated": graph.truncated,
        "data_notice": data_notice(provenances),
    }


def path_result(
    source_id: str,
    target_id: str,
    nodes: list[Entity],
    hops: list[Relationship],
    *,
    found: bool,
) -> dict[str, Any]:
    provenances = [e.provenance for e in nodes] + [r.provenance for r in hops]
    return {
        "source_id": source_id,
        "target_id": target_id,
        "found": found,
        "entity_ids": [e.id for e in nodes],
        "entities": [entity(e) for e in nodes],
        "relationships": [relationship(r) for r in hops],
        "data_notice": data_notice(provenances),
    }


def timeline_list(
    events: list[TimelineEvent],
    *,
    include_predictions: bool,
    limit: int,
    total: int,
    next_cursor: str | None,
) -> dict[str, Any]:
    return {
        "items": [timeline_event(e) for e in events],
        "page": {"limit": limit, "total": total, "next_cursor": next_cursor},
        "include_predictions": include_predictions,
        "data_notice": data_notice(e.provenance for e in events),
    }


def graph_overview(overview: GraphOverview, extra_provenances: Iterable[Provenance] = ()) -> dict[str, Any]:
    provenances = [r.entity.provenance for r in overview.top_entities] + list(extra_provenances)
    return {
        "entity_count": overview.entity_count,
        "relationship_count": overview.relationship_count,
        "component_count": overview.component_count,
        "top_entities": [
            {"entity": entity(r.entity), "score": round(r.score, SCORE_DIGITS)} for r in overview.top_entities
        ],
        "clusters": [
            {"id": c.id, "label": c.label, "size": c.size, "member_ids": list(c.member_ids)}
            for c in overview.clusters
        ],
        "score_method": "pagerank",
        "score_provenance": Provenance.ESTIMATED.value,
        "data_notice": data_notice(provenances),
    }


def github_target(target: GitHubTarget) -> dict[str, Any]:
    owner_url = f"https://github.com/{target.owner}"
    return {
        "kind": target.kind.value,
        "owner": target.owner,
        "repo": target.repo,
        "number": target.number,
        "ref": target.ref,
        "html_url": f"{owner_url}/{target.repo}" if target.repo else owner_url,
    }


def _timestamp(value: datetime | None) -> str | None:
    """RFC 3339 in UTC to whole seconds, e.g. ``2026-10-21T10:15:30Z``."""
    if value is None:
        return None
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repository_analysis(result: RepoAnalysisResult) -> dict[str, Any]:
    """A live repository read, shaped for ``RepositoryAnalysis``.

    The label is ``sourced`` because the response carries its own evidence: ``source_urls`` names the exact
    GitHub API URLs read and ``fetched_at`` says when. Nothing is stored, so no evidence row exists. The text
    caps below are belt and braces; GitHub's own limits are lower.
    """
    repo = result.snapshot
    return {
        "full_name": repo.full_name,
        "html_url": f"https://github.com/{repo.full_name}",
        "description": repo.description[:1000] if repo.description else None,
        "default_branch": repo.default_branch,
        "primary_language": repo.primary_language[:100] if repo.primary_language else None,
        "languages": dict(result.languages),
        "topics": list(repo.topics),
        "license_spdx": repo.license_spdx,
        "license_name": repo.license_name[:100] if repo.license_name else None,
        "stars": repo.stars,
        "forks": repo.forks,
        "open_issues": repo.open_issues,
        "archived": repo.archived,
        "is_fork": repo.is_fork,
        "created_at": _timestamp(repo.created_at),
        "pushed_at": _timestamp(repo.pushed_at),
        "fetched_at": _timestamp(result.fetched_at),
        "cache_status": result.cache_status.value,
        "source_urls": list(result.source_urls),
        "provenance": Provenance.SOURCED.value,
    }


def repository_activity(result: RepoActivityResult) -> dict[str, Any]:
    """The opt-in contributors and latest-release read, shaped for ``RepositoryActivity``.

    ``sourced`` for the same reason as the analysis: ``source_urls`` names every GitHub URL the values rest on.
    ``contributors_status`` says which of GitHub's three answers the list is (listed, none, declined), so an
    empty list is never ambiguous. Logins and the release name are capped again here; they are text, not markup.
    """
    release = result.latest_release
    return {
        "full_name": result.full_name,
        "html_url": f"https://github.com/{result.full_name}",
        "contributors_status": result.contributors_status.value,
        "contributors": [
            {
                "login": person.login[:100],
                "contributions": person.contributions,
                "is_bot": person.is_bot,
                "profile_url": person.profile_url,
            }
            for person in result.contributors
        ],
        "latest_release": None
        if release is None
        else {
            "tag_name": release.tag_name[:255],
            "name": release.name[:200] if release.name else None,
            "published_at": _timestamp(release.published_at),
            "prerelease": release.prerelease,
            "html_url": release.html_url,
        },
        "fetched_at": _timestamp(result.fetched_at),
        "cache_status": result.cache_status.value,
        "source_urls": list(result.source_urls),
        "provenance": Provenance.SOURCED.value,
    }


def commit_activity(result: CommitActivityResult) -> dict[str, Any]:
    """The opt-in weekly commit counts, shaped for ``CommitActivity``.

    ``status`` is which of GitHub's answers this is (ready, computing, none), so an empty ``weeks`` list is never
    ambiguous. ``sourced`` because ``source_urls`` names every GitHub URL the counts rest on. Nothing is summed
    or smoothed here: each week is GitHub's own.
    """
    return {
        "full_name": result.full_name,
        "html_url": f"https://github.com/{result.full_name}",
        "status": result.status.value,
        "weeks": [
            {"week_start": _timestamp(week.week_start), "days": list(week.days), "total": week.total}
            for week in result.weeks
        ],
        "fetched_at": _timestamp(result.fetched_at),
        "cache_status": result.cache_status.value,
        "source_urls": list(result.source_urls),
        "provenance": Provenance.SOURCED.value,
    }


def package_report(result: PackageResult) -> dict[str, Any]:
    """One package's declared dependencies, shaped for ``PackageReport``.

    ``sourced`` because ``source_url`` is the registry address the values were read from and the package's own
    metadata states them. It says nothing about the package being maintained, popular or safe.
    """
    snapshot = result.snapshot
    return {
        "registry": snapshot.registry.value,
        "name": snapshot.name,
        "version": snapshot.version,
        "summary": snapshot.summary,
        "license": snapshot.license,
        "homepage": snapshot.homepage,
        "registry_url": snapshot.registry_url,
        "dependencies": [
            {"name": ref.name, "requirement": ref.requirement, "readable": ref.readable}
            for ref in snapshot.dependencies
        ],
        "left_out": [{"kind": kind, "count": count} for kind, count in snapshot.left_out],
        "fetched_at": _timestamp(snapshot.fetched_at),
        "cache_status": result.cache_status.value,
        "source_url": snapshot.source_url,
        "provenance": Provenance.SOURCED.value,
    }


def package_releases(result: ReleaseResult) -> dict[str, Any]:
    """One package's release dates, shaped for ``PackageReleases``.

    ``sourced`` because ``source_url`` is the registry address the dates were read from and the registry records
    them. It says nothing about how the package is maintained.
    """
    history = result.history
    return {
        "registry": history.registry.value,
        "name": history.name,
        "registry_url": history.registry_url,
        "releases": [
            {
                "version": record.version,
                "released_at": _timestamp(record.released_at),
                "pre_release": record.pre_release,
                "status": record.status,
            }
            for record in history.releases
        ],
        "omitted_older": history.omitted_older,
        "left_out": [{"kind": kind, "count": count} for kind, count in history.left_out],
        "date_meaning": history.date_meaning,
        "fetched_at": _timestamp(history.fetched_at),
        "cache_status": result.cache_status.value,
        "source_url": history.source_url,
        "provenance": Provenance.SOURCED.value,
    }


def rate_limit_status(state: RateLimitState) -> dict[str, Any]:
    reset = datetime.fromtimestamp(state.reset_epoch, UTC) if state.reset_epoch is not None else None
    return {
        "limit": state.limit,
        "remaining": state.remaining,
        "used": state.used,
        "reset_at": _timestamp(reset),
        "resource": state.resource,
    }
