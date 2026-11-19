"""JSON Schema (OpenAPI 3.1 dialect) components for the HTTP contract.

These dicts are the single description of every request and response body. They feed three things:
the published OpenAPI document, the query-parameter parser, and tests that validate real serializer
output. Enumerations are generated from the Python domain enums so the contract cannot drift from
the domain. Components marked "planned" describe routes that are specified but not implemented.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from vyron_api.api.errors import ErrorCode
from vyron_api.api.limits import (
    MAX_CLUSTER_MEMBERS,
    MAX_CURSOR_LENGTH,
    MAX_GRAPH_DEPTH,
    MAX_GRAPH_EDGES,
    MAX_GRAPH_NODES,
    MAX_PAGE_SIZE,
    MAX_PATH_DEPTH,
)
from vyron_api.domain.entities import EntityKind, RelationType
from vyron_api.domain.enums import (
    DatePrecision,
    JobStatus,
    MapMode,
    Reliability,
    ResearchKind,
    ResearchStatus,
    SourceType,
)
from vyron_api.domain.provenance import Provenance
from vyron_api.github.url import TargetKind

Schema = dict[str, Any]

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


# ---- builders ---------------------------------------------------------------------------------
def ref(name: str) -> Schema:
    return {"$ref": f"#/components/schemas/{name}"}


def enum_of(members: type[StrEnum] | Iterable[str], **extra: Any) -> Schema:
    values = [m.value for m in members] if isinstance(members, type) else list(members)
    return {"type": "string", "enum": values, **extra}


def string(
    *, min_length: int | None = None, max_length: int | None = None, pattern: str | None = None,
    format: str | None = None, description: str | None = None,
) -> Schema:  # fmt: skip
    schema: Schema = {"type": "string"}
    if min_length is not None:
        schema["minLength"] = min_length
    if max_length is not None:
        schema["maxLength"] = max_length
    if pattern is not None:
        schema["pattern"] = pattern
    if format is not None:
        schema["format"] = format
    if description is not None:
        schema["description"] = description
    return schema


def integer(minimum: int | None = None, maximum: int | None = None, **extra: Any) -> Schema:
    schema: Schema = {"type": "integer", **extra}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def boolean(**extra: Any) -> Schema:
    return {"type": "boolean", **extra}


def array(
    items: Schema,
    *,
    max_items: int | None = None,
    min_items: int | None = None,
    description: str | None = None,
) -> Schema:
    schema: Schema = {"type": "array", "items": items}
    if max_items is not None:
        schema["maxItems"] = max_items
    if min_items is not None:
        schema["minItems"] = min_items
    if description is not None:
        schema["description"] = description
    return schema


def nullable(schema: Schema) -> Schema:
    """``schema`` or null, in OpenAPI 3.1 / JSON Schema 2020-12 style."""
    if "type" in schema and isinstance(schema["type"], str) and "$ref" not in schema:
        return {**schema, "type": [schema["type"], "null"]}
    return {"anyOf": [schema, {"type": "null"}]}


def obj(
    properties: Mapping[str, Schema], *, optional: Sequence[str] = (), description: str | None = None
) -> Schema:
    """A closed object. Every property is required unless named in ``optional``."""
    unknown = set(optional) - set(properties)
    if unknown:
        raise ValueError(f"optional names not in properties: {sorted(unknown)}")
    schema: Schema = {
        "type": "object",
        "properties": dict(properties),
        "required": [name for name in properties if name not in optional],
        "additionalProperties": False,
    }
    if description is not None:
        schema["description"] = description
    return schema


# ---- shared primitives ------------------------------------------------------------------------
ID: Schema = {"type": "string", "format": "uuid"}
SLUG: Schema = string(min_length=1, max_length=100, pattern=SLUG_PATTERN)
TIMESTAMP: Schema = {"type": "string", "format": "date-time"}
DATA_NOTICE: Schema = nullable(
    string(
        max_length=300,
        description="Present when any returned item is development seed data rather than a production fact.",
    )
)
CURSOR: Schema = string(min_length=1, max_length=MAX_CURSOR_LENGTH)
EVIDENCE_IDS: Schema = array(ID, max_items=50)


def build_components() -> dict[str, Schema]:
    c: dict[str, Schema] = {}

    # enumerations (generated from the domain, never retyped)
    c["EntityKind"] = enum_of(EntityKind)
    c["RelationType"] = enum_of(RelationType)
    c["Provenance"] = enum_of(
        Provenance,
        description="How a statement is backed. Only 'sourced' requires evidence; the rest are labels.",
    )
    c["DatePrecision"] = enum_of(DatePrecision)
    c["MapMode"] = enum_of(MapMode)
    c["ResearchKind"] = enum_of(ResearchKind)
    c["ResearchStatus"] = enum_of(ResearchStatus)
    c["JobStatus"] = enum_of(JobStatus)
    c["SourceType"] = enum_of(SourceType)
    c["Reliability"] = enum_of(Reliability)
    c["ErrorCode"] = enum_of(ErrorCode)
    c["GitHubTargetKind"] = enum_of(TargetKind)

    # errors and paging
    c["Error"] = obj(
        {
            "error": obj(
                {
                    "code": ref("ErrorCode"),
                    "message": string(max_length=500),
                    "request_id": string(min_length=1, max_length=64),
                    "details": array(
                        obj({"field": string(max_length=200), "issue": string(max_length=500)}),
                        max_items=20,
                    ),
                },
                optional=("details",),
            )
        }
    )
    c["PageInfo"] = obj(
        {
            "limit": integer(1, MAX_PAGE_SIZE),
            "total": integer(0),
            "next_cursor": nullable(CURSOR),
        }
    )

    # graph vocabulary
    c["Entity"] = obj(
        {
            "id": ID,
            "kind": ref("EntityKind"),
            "slug": SLUG,
            "name": string(min_length=1, max_length=200),
            "aliases": array(string(min_length=1, max_length=80), max_items=50),
            "provenance": ref("Provenance"),
        }
    )
    c["EntityList"] = obj(
        {"items": array(ref("Entity"), max_items=MAX_PAGE_SIZE), "page": ref("PageInfo"), "data_notice": DATA_NOTICE}
    )
    c["Relationship"] = obj(
        {
            "source_id": ID,
            "target_id": ID,
            "type": ref("RelationType"),
            "provenance": ref("Provenance"),
            "evidence_ids": EVIDENCE_IDS,
        }
    )
    c["Subgraph"] = obj(
        {
            "center_id": ID,
            "depth": integer(0, MAX_GRAPH_DEPTH),
            "entities": array(ref("Entity"), max_items=MAX_GRAPH_NODES),
            "relationships": array(ref("Relationship"), max_items=MAX_GRAPH_EDGES),
            "truncated": boolean(description="True when the node or edge limit cut the result short."),
            "data_notice": DATA_NOTICE,
        }
    )
    c["PathResult"] = obj(
        {
            "source_id": ID,
            "target_id": ID,
            "found": boolean(),
            "entity_ids": array(
                ID,
                max_items=MAX_PATH_DEPTH + 1,
                description="Entities along the path, in order; empty when not found.",
            ),
            "entities": array(ref("Entity"), max_items=MAX_PATH_DEPTH + 1),
            "relationships": array(
                ref("Relationship"), max_items=MAX_PATH_DEPTH, description="One relationship per hop."
            ),
            "data_notice": DATA_NOTICE,
        }
    )
    c["GraphOverview"] = obj(
        {
            "entity_count": integer(0),
            "relationship_count": integer(0),
            "component_count": integer(0),
            "top_entities": array(
                obj({"entity": ref("Entity"), "score": {"type": "number", "minimum": 0, "maximum": 1}}),
                max_items=20,
            ),
            "clusters": array(
                obj(
                    {
                        "id": ID,
                        "label": string(max_length=200),
                        "size": integer(1),
                        "member_ids": array(
                            ID,
                            max_items=MAX_CLUSTER_MEMBERS,
                            description="Most central members first. Fewer than size means the list was cut.",
                        ),
                    }
                ),
                max_items=20,
            ),
            "score_method": {"type": "string", "const": "pagerank"},
            "score_provenance": {"type": "string", "const": "estimated"},
            "data_notice": DATA_NOTICE,
        }
    )

    # timeline
    c["TimelineEvent"] = obj(
        {
            "id": ID,
            "entity_id": ID,
            "occurred_on": {"type": "string", "format": "date"},
            "date_precision": ref("DatePrecision"),
            "title": string(min_length=1, max_length=300),
            "event_type": string(min_length=1, max_length=60),
            "provenance": ref("Provenance"),
            "is_prediction": boolean(description="True exactly when provenance is 'predicted'."),
            "description": nullable(string(max_length=2000)),
        }
    )
    c["TimelineList"] = obj(
        {
            "items": array(ref("TimelineEvent"), max_items=MAX_PAGE_SIZE),
            "page": ref("PageInfo"),
            "include_predictions": boolean(),
            "data_notice": DATA_NOTICE,
        }
    )

    # GitHub URL parsing (pure; performs no network request)
    c["GitHubTarget"] = obj(
        {
            "kind": ref("GitHubTargetKind"),
            "owner": string(min_length=1, max_length=39),
            "repo": nullable(string(min_length=1, max_length=100)),
            "number": nullable(integer(0)),
            "ref": nullable(string(max_length=300)),
            "html_url": {"type": "string", "format": "uri", "maxLength": 300},
        }
    )

    # service metadata
    c["Meta"] = obj(
        {
            "name": {"type": "string", "const": "VYRON"},
            "api_version": string(pattern=r"^v[0-9]+$"),
            "data_mode": enum_of(("seed", "database")),
            "data_notice": DATA_NOTICE,
            "enums": obj(
                {
                    "entity_kinds": array(ref("EntityKind"), max_items=len(EntityKind)),
                    "relation_types": array(ref("RelationType"), max_items=len(RelationType)),
                    "provenance": array(ref("Provenance"), max_items=len(Provenance)),
                }
            ),
            "limits": obj(
                {
                    "max_page_size": integer(1),
                    "default_page_size": integer(1),
                    "max_graph_depth": integer(1),
                    "max_graph_nodes": integer(1),
                    "max_path_depth": integer(1),
                }
            ),
        }
    )
    c["Health"] = obj(
        {
            "status": enum_of(("ok", "degraded")),
            "checks": {
                "type": "object",
                "additionalProperties": enum_of(("ok", "unavailable", "not_configured")),
            },
        }
    )

    # ---- planned (specified, not implemented) -------------------------------------------------
    c["Credentials"] = obj(
        {
            "email": string(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
            "password": string(min_length=12, max_length=128),
        }
    )
    c["UserProfile"] = obj(
        {
            "id": ID,
            "email": string(max_length=254),
            "display_name": nullable(string(max_length=100)),
            "role": enum_of(("user", "admin")),
            "created_at": TIMESTAMP,
        }
    )
    c["Source"] = obj(
        {
            "id": ID,
            "url": {"type": "string", "format": "uri", "maxLength": 2048},
            "title": string(min_length=1, max_length=500),
            "source_type": ref("SourceType"),
            "publisher": nullable(string(max_length=200)),
            "published_on": nullable({"type": "string", "format": "date"}),
            "reliability": ref("Reliability"),
            "retrieved_at": TIMESTAMP,
        }
    )
    c["Evidence"] = obj(
        {
            "id": ID,
            "entity_id": ID,
            "claim": string(min_length=1, max_length=1000),
            "locator": nullable(string(max_length=300)),
            "retrieved_at": TIMESTAMP,
            "source": ref("Source"),
        }
    )
    c["EvidenceList"] = obj({"items": array(ref("Evidence")), "page": ref("PageInfo")})
    c["RepositoryAnalysis"] = obj(
        {
            "full_name": string(min_length=3, max_length=140),
            "html_url": {"type": "string", "format": "uri", "maxLength": 300},
            "description": nullable(string(max_length=1000)),
            "default_branch": string(max_length=255),
            "primary_language": nullable(string(max_length=100)),
            "languages": {"type": "object", "additionalProperties": integer(0), "maxProperties": 200},
            "topics": array(string(max_length=100), max_items=100),
            "license_spdx": nullable(string(max_length=100)),
            "license_name": nullable(string(max_length=100)),
            "stars": integer(0),
            "forks": integer(0),
            "open_issues": integer(0),
            "archived": boolean(),
            "is_fork": boolean(),
            "created_at": nullable(TIMESTAMP),
            "pushed_at": nullable(TIMESTAMP),
            "fetched_at": TIMESTAMP,
            "cache_status": enum_of(("fresh", "revalidated", "stale")),
            "source_urls": array(
                {"type": "string", "format": "uri", "maxLength": 300},
                min_items=1,
                max_items=4,
                description="The GitHub API URLs the values were read from: the evidence for the label.",
            ),
            "provenance": ref("Provenance"),
        }
    )
    c["Contributor"] = obj(
        {
            "login": string(min_length=1, max_length=100),
            "contributions": integer(0, description="Commits GitHub attributes to the account, as it reports them."),
            "is_bot": boolean(),
            "profile_url": nullable(
                {
                    "type": "string",
                    "format": "uri",
                    "maxLength": 300,
                    "description": "Built by VYRON from a login that passes GitHub's naming rules. Null for bots.",
                }
            ),
        }
    )
    c["LatestRelease"] = obj(
        {
            "tag_name": string(min_length=1, max_length=255),
            "name": nullable(string(max_length=200)),
            "published_at": nullable(TIMESTAMP),
            "prerelease": boolean(),
            "html_url": {"type": "string", "format": "uri", "maxLength": 300},
        },
        description="GitHub's 'latest release': the most recent release that is not a draft or pre-release, ordered by "
        "its created date (the date of the commit the release uses). That is GitHub's definition, not necessarily "
        "the highest version number.",
    )
    c["RepositoryActivity"] = obj(
        {
            "full_name": string(min_length=3, max_length=140),
            "html_url": {"type": "string", "format": "uri", "maxLength": 300},
            "contributors_status": enum_of(("listed", "none", "declined")),
            "contributors": array(ref("Contributor"), max_items=10),
            "latest_release": nullable(ref("LatestRelease")),
            "fetched_at": TIMESTAMP,
            "cache_status": enum_of(("fresh", "revalidated", "stale")),
            "source_urls": array(
                {"type": "string", "format": "uri", "maxLength": 300},
                min_items=1,
                max_items=4,
                description="Every GitHub API URL the values rest on, including one whose answer was a 403 or 404.",
            ),
            "provenance": ref("Provenance"),
        }
    )
    c["CommitWeek"] = obj(
        {
            "week_start": TIMESTAMP,
            "days": array(integer(0), min_items=7, max_items=7, description="Commits on each day, Sunday first."),
            "total": integer(0, description="GitHub's total for the week; it equals the sum of `days`."),
        },
        description="One week of commits on the default branch, starting on a Sunday at 00:00 UTC.",
    )
    c["CommitActivity"] = obj(
        {
            "full_name": string(min_length=3, max_length=140),
            "html_url": {"type": "string", "format": "uri", "maxLength": 300},
            "status": enum_of(("ready", "computing", "none")),
            "weeks": array(ref("CommitWeek"), max_items=60, description="Oldest week first. Empty unless `ready`."),
            "fetched_at": TIMESTAMP,
            "cache_status": enum_of(("fresh", "revalidated", "stale")),
            "source_urls": array(
                {"type": "string", "format": "uri", "maxLength": 300},
                min_items=1,
                max_items=4,
                description="Every GitHub API URL the values rest on, including one whose answer was a 202 or 204.",
            ),
            "provenance": ref("Provenance"),
        }
    )
    c["PackageDependency"] = obj(
        {
            "name": string(min_length=1, max_length=214, description="What to ask the registry for."),
            "requirement": nullable(
                string(
                    max_length=100,
                    description="The version range as the package declares it. Null when none is given or when "
                    "it is not plain version syntax.",
                )
            ),
            "readable": boolean(
                description="False when the requirement points outside the registry (a URL, a path, an alias), so "
                "the name does not identify a package there."
            ),
        }
    )
    c["PackageLeftOut"] = obj(
        {
            "kind": enum_of(("conditional", "development", "peer", "optional", "unusable")),
            "count": integer(1),
        },
        description="Declared by the package but not listed in `dependencies`. `conditional` is a PyPI requirement "
        "under an extra or an environment marker; `unusable` is a name no registry could have issued.",
    )
    c["PackageReport"] = obj(
        {
            "registry": enum_of(("pypi", "npm")),
            "name": string(min_length=1, max_length=214),
            "version": string(min_length=1, max_length=100),
            "summary": nullable(string(max_length=500)),
            "license": nullable(string(max_length=64, description="As the maintainer wrote it; not validated.")),
            "homepage": nullable({"type": "string", "format": "uri", "maxLength": 300}),
            "registry_url": nullable(
                {
                    "type": "string",
                    "format": "uri",
                    "maxLength": 300,
                    "description": "The package's page for people, built by VYRON from the checked name. Null when "
                    "the values came from a stand-in rather than the registry.",
                }
            ),
            "dependencies": array(ref("PackageDependency"), max_items=500),
            "left_out": array(ref("PackageLeftOut"), max_items=5),
            "fetched_at": TIMESTAMP,
            "cache_status": enum_of(("fresh", "stale")),
            "source_url": {
                "type": "string",
                "format": "uri",
                "maxLength": 300,
                "description": "The registry API address the values were read from: the evidence for the label.",
            },
            "provenance": ref("Provenance"),
        }
    )
    c["ReleaseEntry"] = obj(
        {
            "version": string(min_length=1, max_length=100),
            "released_at": TIMESTAMP,
            "pre_release": boolean(
                description="True when the version NUMBER looks like a pre-release (a PEP 440 a/b/rc/dev segment for "
                "PyPI, a `-` suffix for npm). A reading of the string, not something the registry says."
            ),
            "status": enum_of(("normal", "yanked", "deprecated")),
        },
        description="One version and the date its registry records for it. `yanked` is PyPI's flag (every file of the "
        "version is yanked); `deprecated` is npm's.",
    )
    c["ReleaseLeftOut"] = obj(
        {
            "kind": enum_of(("undated", "removed", "implausible_date", "unusable")),
            "count": integer(1),
        },
        description="Versions the registry names that are not in `releases`. `undated`: no usable date came with the "
        "version. `removed`: npm still holds a date for a version that is no longer published. `implausible_date`: "
        "before 1991 or after the moment of the read. `unusable`: a version string no registry would issue.",
    )
    c["PackageReleases"] = obj(
        {
            "registry": enum_of(("pypi", "npm")),
            "name": string(min_length=1, max_length=214),
            "registry_url": nullable(
                {
                    "type": "string",
                    "format": "uri",
                    "maxLength": 300,
                    "description": "The package's page for people. Null when the values came from a stand-in.",
                }
            ),
            "releases": array(ref("ReleaseEntry"), max_items=2000, description="Oldest first."),
            "omitted_older": integer(0, description="Earlier releases left out to keep the list to 2000 entries."),
            "left_out": array(ref("ReleaseLeftOut"), max_items=4),
            "date_meaning": enum_of(("first_file_upload", "publish_time")),
            "fetched_at": TIMESTAMP,
            "cache_status": enum_of(("fresh", "stale")),
            "source_url": {
                "type": "string",
                "format": "uri",
                "maxLength": 300,
                "description": "The registry API address the values were read from: the evidence for the label.",
            },
            "provenance": ref("Provenance"),
        },
        description="`date_meaning` says what a date is: `first_file_upload` (PyPI: the earliest upload time among a "
        "version's files) or `publish_time` (npm: the registry's own `time` entry).",
    )
    c["RateLimitStatus"] = obj(
        {
            "limit": nullable(integer(0)),
            "remaining": nullable(integer(0)),
            "used": nullable(integer(0)),
            "reset_at": nullable(TIMESTAMP),
            "resource": nullable(string(max_length=40)),
        }
    )
    c["ResearchRequest"] = obj(
        {
            "kind": ref("ResearchKind"),
            "question": nullable(string(min_length=1, max_length=1000)),
            "entity_ids": array(ID, min_items=1, max_items=10),
        },
        optional=("question",),
    )
    c["ResearchClaim"] = obj(
        {
            "text": string(min_length=1, max_length=1000),
            "provenance": ref("Provenance"),
            "evidence_ids": EVIDENCE_IDS,
            "confidence": nullable({"type": "number", "minimum": 0, "maximum": 1}),
        }
    )
    c["ResearchRun"] = obj(
        {
            "id": ID,
            "kind": ref("ResearchKind"),
            "status": ref("ResearchStatus"),
            "created_at": TIMESTAMP,
            "finished_at": nullable(TIMESTAMP),
            "summary": nullable(string(max_length=4000)),
            "claims": array(ref("ResearchClaim")),
            "uncertainty": array(string(max_length=1000)),
            "rejected_citation_count": integer(0),
            "failure_reason": nullable(string(max_length=300)),
        }
    )
    c["Bookmark"] = obj({"entity": ref("Entity"), "created_at": TIMESTAMP})
    c["BookmarkList"] = obj({"items": array(ref("Bookmark")), "page": ref("PageInfo")})
    c["SavedMapInput"] = obj(
        {
            "name": string(min_length=1, max_length=120),
            "mode": ref("MapMode"),
            "entity_ids": array(ID, max_items=200),
        }
    )
    c["SavedMap"] = obj(
        {
            "id": ID,
            "name": string(min_length=1, max_length=120),
            "mode": ref("MapMode"),
            "entity_ids": array(ID, max_items=200),
            "created_at": TIMESTAMP,
        }
    )
    c["SavedMapList"] = obj({"items": array(ref("SavedMap")), "page": ref("PageInfo")})
    return c


COMPONENTS: Mapping[str, Schema] = build_components()

PLANNED_COMPONENTS = frozenset(
    {
        "Credentials", "UserProfile", "Source", "Evidence", "EvidenceList", "ResearchRequest",
        "ResearchClaim", "ResearchRun", "Bookmark", "BookmarkList", "SavedMapInput", "SavedMap",
        "SavedMapList",
    }
)  # fmt: skip
