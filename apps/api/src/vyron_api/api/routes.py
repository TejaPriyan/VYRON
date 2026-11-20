"""The route registry: the single list of HTTP operations VYRON exposes or has specified.

OpenAPI generation, the dispatcher, the FastAPI binding and the docs all read this list, so they
cannot disagree. ``Status.PLANNED`` routes are part of the contract (schemas, auth, limits) but have
no implementation yet; the dispatcher answers them with 501 ``not_implemented``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from vyron_api.api import limits
from vyron_api.api.schemas import CURSOR, ID, SLUG, Schema, boolean, enum_of, integer, ref, string

PREFIX = limits.API_PREFIX


class Status(StrEnum):
    IMPLEMENTED = "implemented"
    PLANNED = "planned"


class Auth(StrEnum):
    NONE = "none"
    SESSION = "session"  # cookie session; unsafe methods also need the CSRF header


class RateBucket(StrEnum):
    HEALTH = "health"
    READ = "read"
    SEARCH = "search"
    GRAPH = "graph"
    GITHUB = "github"
    REGISTRY = "registry"
    AUTH = "auth"  # strictest: credential endpoints
    AI = "ai"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    location: str  # "path" | "query"
    schema: Schema
    required: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if self.location not in {"path", "query"}:
            raise ValueError(f"unsupported parameter location: {self.location}")
        if self.location == "path" and not self.required:
            raise ValueError("path parameters are always required")


@dataclass(frozen=True, slots=True)
class Route:
    operation_id: str
    method: str
    path: str  # OpenAPI template, e.g. /api/v1/entities/{entity_id}
    summary: str
    tags: tuple[str, ...]
    status: Status = Status.IMPLEMENTED
    params: tuple[Param, ...] = ()
    request_schema: str | None = None
    response_schema: str | None = None
    success_status: int = 200
    auth: Auth = Auth.NONE
    rate_bucket: RateBucket = RateBucket.READ
    cache_seconds: int | None = None  # public Cache-Control max-age for GET; None means no-store
    description: str = ""
    _pattern: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.method not in {"GET", "POST", "PUT", "DELETE"}:
            raise ValueError(f"unsupported method {self.method}")
        names = re.findall(r"\{(\w+)\}", self.path)
        declared = [p.name for p in self.params if p.location == "path"]
        if sorted(names) != sorted(declared):
            raise ValueError(f"{self.operation_id}: path params {names} != declared {declared}")
        if self.method == "GET" and self.request_schema:
            raise ValueError(f"{self.operation_id}: GET cannot have a body")
        if self.method != "GET" and self.cache_seconds is not None:
            raise ValueError(f"{self.operation_id}: only GET responses may be cached")
        pieces = re.split(r"(\{\w+\})", self.path)
        pattern = "".join(
            f"(?P<{piece[1:-1]}>[^/]+)" if piece.startswith("{") else re.escape(piece) for piece in pieces
        )
        object.__setattr__(self, "_pattern", re.compile(f"^{pattern}$"))

    @property
    def path_params(self) -> tuple[Param, ...]:
        return tuple(p for p in self.params if p.location == "path")

    @property
    def query_params(self) -> tuple[Param, ...]:
        return tuple(p for p in self.params if p.location == "query")

    @property
    def is_public(self) -> bool:
        return self.auth is Auth.NONE

    def match(self, path: str) -> dict[str, str] | None:
        found = self._pattern.match(path)
        return found.groupdict() if found else None


# ---- parameters ---------------------------------------------------------------------------------
def _path(name: str, schema: Schema, description: str = "") -> Param:
    return Param(name, "path", schema, True, description)


def _query(name: str, schema: Schema, description: str = "", *, required: bool = False) -> Param:
    return Param(name, "query", schema, required, description)


ENTITY_ID = _path("entity_id", ID, "Entity id (UUID).")
KIND_PATH = _path("kind", ref("EntityKind"))
SLUG_PATH = _path("slug", SLUG)
LIMIT = _query(
    "limit",
    integer(1, limits.MAX_PAGE_SIZE, default=limits.DEFAULT_PAGE_SIZE),
    "Page size.",
)
CURSOR_Q = _query("cursor", CURSOR, "Opaque cursor from a previous response's page.next_cursor.")
KIND_Q = _query(
    "kind",
    {"type": "array", "items": ref("EntityKind"), "maxItems": 14, "uniqueItems": True},
    "Restrict to these kinds (repeat the parameter).",
)
RELATION_Q = _query(
    "relation_type",
    {"type": "array", "items": ref("RelationType"), "maxItems": 20, "uniqueItems": True},
    "Only follow these relationship types (repeat the parameter).",
)
LIST_PARAMS = (KIND_Q, LIMIT, CURSOR_Q)

_TECH_TAG, _COMPANY_TAG = ("technologies",), ("companies",)


def _implemented() -> tuple[Route, ...]:
    return (
        Route(
            "health_live",
            "GET",
            "/health/live",
            "Liveness probe",
            ("system",),
            response_schema="Health",
            rate_bucket=RateBucket.HEALTH,
            description="Reports only that the process is serving requests.",
        ),
        Route(
            "health_ready",
            "GET",
            "/health/ready",
            "Readiness probe",
            ("system",),
            response_schema="Health",
            rate_bucket=RateBucket.HEALTH,
            description="Reports the state of each backing service; 503 when the graph is unavailable.",
        ),
        Route(
            "get_meta",
            "GET",
            f"{PREFIX}/meta",
            "Service metadata, vocabularies and limits",
            ("system",),
            response_schema="Meta",
            cache_seconds=300,
        ),
        Route(
            "list_entities",
            "GET",
            f"{PREFIX}/entities",
            "List entities by name",
            ("entities",),
            params=LIST_PARAMS,
            response_schema="EntityList",
            cache_seconds=30,
        ),
        Route(
            "get_entity",
            "GET",
            f"{PREFIX}/entities/{{entity_id}}",
            "Get one entity",
            ("entities",),
            params=(ENTITY_ID,),
            response_schema="Entity",
            cache_seconds=30,
        ),
        Route(
            "get_entity_by_slug",
            "GET",
            f"{PREFIX}/entities/by-slug/{{kind}}/{{slug}}",
            "Get one entity by kind and slug",
            ("entities",),
            params=(KIND_PATH, SLUG_PATH),
            response_schema="Entity",
            cache_seconds=30,
        ),
        Route(
            "get_entity_neighbors",
            "GET",
            f"{PREFIX}/entities/{{entity_id}}/neighbors",
            "Entities connected to this one",
            ("entities", "graph"),
            params=(
                ENTITY_ID,
                _query(
                    "depth",
                    integer(0, limits.MAX_GRAPH_DEPTH, default=limits.DEFAULT_GRAPH_DEPTH),
                    "Hops to follow; relationships are traversed in both directions.",
                ),
                RELATION_Q,
                _query(
                    "max_nodes",
                    integer(1, limits.MAX_GRAPH_NODES, default=limits.DEFAULT_GRAPH_NODES),
                    "Node limit; the response says when it truncated.",
                ),
            ),
            response_schema="Subgraph",
            rate_bucket=RateBucket.GRAPH,
            cache_seconds=30,
        ),
        Route(
            "get_entity_timeline",
            "GET",
            f"{PREFIX}/entities/{{entity_id}}/timeline",
            "Timeline events for one entity",
            ("entities", "timeline"),
            params=(
                ENTITY_ID,
                _query(
                    "include_predictions",
                    boolean(default=False),
                    "Predictions are labelled and only returned on request.",
                ),
                LIMIT,
                CURSOR_Q,
            ),
            response_schema="TimelineList",
            cache_seconds=30,
        ),
        Route(
            "list_technologies",
            "GET",
            f"{PREFIX}/technologies",
            "List technology-like entities",
            _TECH_TAG,
            params=LIST_PARAMS,
            response_schema="EntityList",
            cache_seconds=30,
            description="Languages, frameworks, libraries, databases, platforms, APIs, AI models and "
            "other technologies. ``kind`` may only narrow within those kinds.",
        ),
        Route(
            "list_companies",
            "GET",
            f"{PREFIX}/companies",
            "List companies and organizations",
            _COMPANY_TAG,
            params=LIST_PARAMS,
            response_schema="EntityList",
            cache_seconds=30,
        ),
        Route(
            "search",
            "GET",
            f"{PREFIX}/search",
            "Ranked entity search",
            ("search",),
            params=(
                _query(
                    "q",
                    string(min_length=1, max_length=limits.MAX_QUERY_LENGTH),
                    "Search text; tolerant of case, punctuation and small typos.",
                    required=True,
                ),
                KIND_Q,
                LIMIT,
                CURSOR_Q,
            ),
            response_schema="EntityList",
            rate_bucket=RateBucket.SEARCH,
            cache_seconds=30,
        ),
        Route(
            "graph_overview",
            "GET",
            f"{PREFIX}/graph/overview",
            "Whole-graph statistics, central entities and clusters",
            ("graph",),
            params=(
                _query("top", integer(1, 20, default=10), "Number of central entities."),
                _query("clusters", integer(1, 20, default=8), "Number of clusters, largest first."),
            ),
            response_schema="GraphOverview",
            rate_bucket=RateBucket.GRAPH,
            cache_seconds=60,
        ),
        Route(
            "graph_path",
            "GET",
            f"{PREFIX}/graph/path",
            "Shortest connection between two entities",
            ("graph",),
            params=(
                _query("from", ID, "Start entity id.", required=True),
                _query("to", ID, "End entity id.", required=True),
                _query("max_depth", integer(1, limits.MAX_PATH_DEPTH, default=limits.DEFAULT_PATH_DEPTH)),
            ),
            response_schema="PathResult",
            rate_bucket=RateBucket.GRAPH,
            cache_seconds=30,
        ),
        Route(
            "timeline",
            "GET",
            f"{PREFIX}/timeline",
            "Timeline events across entities",
            ("timeline",),
            params=(
                _query("start_year", integer(limits.MIN_YEAR, limits.MAX_YEAR)),
                _query("end_year", integer(limits.MIN_YEAR, limits.MAX_YEAR)),
                _query("include_predictions", boolean(default=False)),
                LIMIT,
                CURSOR_Q,
            ),
            response_schema="TimelineList",
            cache_seconds=30,
        ),
        Route(
            "github_parse",
            "GET",
            f"{PREFIX}/github/parse",
            "Classify a pasted GitHub link without contacting GitHub",
            ("github",),
            params=(_query("input", string(min_length=1, max_length=300), required=True),),
            response_schema="GitHubTarget",
            rate_bucket=RateBucket.GITHUB,
            description="Pure validation. Only github.com is accepted, so this cannot be used to make "
            "the server request another host.",
        ),
        Route(
            "get_repository_analysis",
            "GET",
            f"{PREFIX}/github/repositories/{{owner}}/{{repo}}",
            "Analyze a public GitHub repository",
            ("github", "repositories"),
            params=(
                _path("owner", string(min_length=1, max_length=39)),
                _path("repo", string(min_length=1, max_length=100)),
            ),
            response_schema="RepositoryAnalysis",
            rate_bucket=RateBucket.GITHUB,
            cache_seconds=60,
            description="Reads the repository from the GitHub REST API when asked, through a short server-side "
            "cache, so values can be up to a minute old; `fetched_at` and `cache_status` say how old. Values "
            "are as GitHub reports them and nothing is stored. Private repositories are reported as not found. "
            "Answers 503 when the server has GitHub access switched off.",
        ),
        Route(
            "get_repository_activity",
            "GET",
            f"{PREFIX}/github/repositories/{{owner}}/{{repo}}/activity",
            "Top contributors and latest release of a public GitHub repository",
            ("github", "repositories"),
            params=(
                _path("owner", string(min_length=1, max_length=39)),
                _path("repo", string(min_length=1, max_length=100)),
            ),
            response_schema="RepositoryActivity",
            rate_bucket=RateBucket.GITHUB,
            cache_seconds=60,
            description="A separate, opt-in read: the ten accounts GitHub lists with the most contributions and "
            "GitHub's 'latest release'. It costs two more GitHub requests (three when the repository copy is "
            "older than a minute), which is why it is not part of the basic analysis. `contributors_status` is "
            "`listed`, `none` (GitHub has nothing to list) or `declined` (GitHub refuses to list them for very "
            "large histories); `latest_release` is null when the repository has no published release. Values "
            "are as GitHub reports them and nothing is stored. Private repositories are reported as not found. "
            "Answers 503 when the server has GitHub access switched off.",
        ),
        Route(
            "get_commit_activity",
            "GET",
            f"{PREFIX}/github/repositories/{{owner}}/{{repo}}/commit-activity",
            "A year of weekly commit counts for a public GitHub repository",
            ("github", "repositories"),
            params=(
                _path("owner", string(min_length=1, max_length=39)),
                _path("repo", string(min_length=1, max_length=100)),
            ),
            response_schema="CommitActivity",
            rate_bucket=RateBucket.GITHUB,
            description="A separate, opt-in read: commits on the default branch for each of the last 52 weeks, as "
            "GitHub reports them. GitHub computes these in the background, so the first ask can only be "
            "answered `status: computing` (GitHub's own 202); ask again a few seconds later. VYRON never waits "
            "for it. `none` means GitHub has no commit activity to report. Every ask, `computing` included, "
            "costs GitHub requests from the server's quota: one more than the repository read, which is "
            "itself reused for a minute. Only a ready answer is kept (for a minute); `computing` is never "
            "cached, in the server or in a browser, which is why this route sends no Cache-Control max-age. "
            "Values are as GitHub reports them and nothing is stored. Private repositories are reported as "
            "not found. Answers 503 when the server has GitHub access switched off.",
        ),
        Route(
            "get_package_dependencies",
            "GET",
            f"{PREFIX}/registries/{{registry}}/package",
            "A package's declared dependencies, read from PyPI or npm",
            ("registries",),
            params=(
                _path("registry", enum_of(("pypi", "npm"))),
                _query("name", string(min_length=1, max_length=214), required=True),
            ),
            response_schema="PackageReport",
            rate_bucket=RateBucket.REGISTRY,
            cache_seconds=60,
            description="Reads one package from its registry's public JSON API when asked, through a short "
            "server-side cache (ten minutes), so values can be that old; `fetched_at` and `cache_status` say how "
            "old. It makes at most one registry request and follows no dependency: reading a dependency's own "
            "dependencies is another call. `dependencies` are the runtime requirements the package declares with "
            "no condition (PyPI) or the `dependencies` of the version tagged `latest` (npm); `left_out` counts "
            "what it declares but this read does not list (PyPI requirements under an extra or an environment "
            "marker; npm development, peer and optional dependencies). `requirement` is the version range as "
            "written, and `readable` is false when it points outside the registry (a URL, a path, an alias). "
            "A `sourced` label means the package's own metadata says it, nothing more: it does not say a "
            "package is maintained, popular or safe. Nothing is stored. Answers 503 when the server has registry "
            "access switched off.",
        ),
        Route(
            "get_package_releases",
            "GET",
            f"{PREFIX}/registries/{{registry}}/releases",
            "A package's release dates, read from PyPI or npm",
            ("registries",),
            params=(
                _path("registry", enum_of(("pypi", "npm"))),
                _query("name", string(min_length=1, max_length=214), required=True),
            ),
            response_schema="PackageReleases",
            rate_bucket=RateBucket.REGISTRY,
            cache_seconds=60,
            description="Every version of one package with the date its registry records for it, oldest first, from "
            "the same registry document as the dependencies route (so one registry request can serve both, and the "
            "server keeps the answer for ten minutes: `fetched_at` and `cache_status` say how old it is). What a date "
            "means differs by registry and is stated in `date_meaning`: PyPI's is the earliest upload time among a "
            "version's files, npm's is the registry's own `time` entry. `pre_release` is read from the version "
            "number only. `left_out` counts versions the registry names that have no usable date, were unpublished "
            "(npm), carry an implausible date or an unusable version string; at most the newest 2000 releases are "
            "listed and `omitted_older` counts the rest. PyPI documents the `releases` key this reads as deprecated, "
            "so if a PyPI answer has no release dates the route answers 502 with that fixed reason. `sourced` means "
            "the registry records it, nothing more: it does not say how a package is maintained. Nothing is stored. "
            "Answers 503 when the server has registry access switched off.",
        ),
        Route(
            "get_github_rate_limit",
            "GET",
            f"{PREFIX}/github/rate-limit",
            "The server's current GitHub API quota",
            ("github",),
            response_schema="RateLimitStatus",
            rate_bucket=RateBucket.GITHUB,
            description="The quota the server's GitHub credentials have, as of the last response from GitHub "
            "(or a live read, which GitHub does not count). Answers 503 when GitHub access is switched off.",
        ),
    )


def _planned() -> tuple[Route, ...]:
    P = Status.PLANNED
    S = Auth.SESSION
    return (
        Route(
            "register",
            "POST",
            f"{PREFIX}/auth/register",
            "Create an account",
            ("auth",),
            P,
            request_schema="Credentials",
            response_schema="UserProfile",
            success_status=201,
            rate_bucket=RateBucket.AUTH,
        ),
        Route(
            "login",
            "POST",
            f"{PREFIX}/auth/login",
            "Start a session",
            ("auth",),
            P,
            request_schema="Credentials",
            response_schema="UserProfile",
            rate_bucket=RateBucket.AUTH,
        ),
        Route(
            "logout",
            "POST",
            f"{PREFIX}/auth/logout",
            "End the session",
            ("auth",),
            P,
            auth=S,
            success_status=204,
            rate_bucket=RateBucket.AUTH,
        ),
        Route(
            "get_me",
            "GET",
            f"{PREFIX}/user/me",
            "The signed-in user's profile",
            ("user",),
            P,
            response_schema="UserProfile",
            auth=S,
        ),
        Route(
            "get_entity_evidence",
            "GET",
            f"{PREFIX}/entities/{{entity_id}}/evidence",
            "Evidence and sources behind an entity's statements",
            ("entities", "evidence"),
            P,
            params=(ENTITY_ID, LIMIT, CURSOR_Q),
            response_schema="EvidenceList",
            cache_seconds=30,
        ),
        Route(
            "create_research_run",
            "POST",
            f"{PREFIX}/research",
            "Start an AI-assisted research run",
            ("research",),
            P,
            request_schema="ResearchRequest",
            response_schema="ResearchRun",
            success_status=202,
            auth=S,
            rate_bucket=RateBucket.AI,
            description="The run answers only from retrieved evidence; unsupported statements are "
            "returned labelled 'ai_generated'.",
        ),
        Route(
            "get_research_run",
            "GET",
            f"{PREFIX}/research/{{run_id}}",
            "Read a research run",
            ("research",),
            P,
            params=(_path("run_id", ID),),
            response_schema="ResearchRun",
            auth=S,
        ),
        Route(
            "create_future_scenario",
            "POST",
            f"{PREFIX}/future/scenarios",
            "Start a Future Lab scenario (always labelled 'predicted')",
            ("future",),
            P,
            request_schema="ResearchRequest",
            response_schema="ResearchRun",
            success_status=202,
            auth=S,
            rate_bucket=RateBucket.AI,
        ),
        Route(
            "list_bookmarks",
            "GET",
            f"{PREFIX}/user/bookmarks",
            "List bookmarked entities",
            ("user",),
            P,
            params=(LIMIT, CURSOR_Q),
            response_schema="BookmarkList",
            auth=S,
        ),
        Route(
            "add_bookmark",
            "PUT",
            f"{PREFIX}/user/bookmarks/{{entity_id}}",
            "Bookmark an entity",
            ("user",),
            P,
            params=(ENTITY_ID,),
            response_schema="Bookmark",
            auth=S,
            rate_bucket=RateBucket.WRITE,
        ),
        Route(
            "remove_bookmark",
            "DELETE",
            f"{PREFIX}/user/bookmarks/{{entity_id}}",
            "Remove a bookmark",
            ("user",),
            P,
            params=(ENTITY_ID,),
            success_status=204,
            auth=S,
            rate_bucket=RateBucket.WRITE,
        ),
        Route(
            "list_saved_maps",
            "GET",
            f"{PREFIX}/user/maps",
            "List saved maps",
            ("user",),
            P,
            params=(LIMIT, CURSOR_Q),
            response_schema="SavedMapList",
            auth=S,
        ),
        Route(
            "create_saved_map",
            "POST",
            f"{PREFIX}/user/maps",
            "Save a map",
            ("user",),
            P,
            request_schema="SavedMapInput",
            response_schema="SavedMap",
            success_status=201,
            auth=S,
            rate_bucket=RateBucket.WRITE,
        ),
    )


ROUTES: tuple[Route, ...] = _implemented() + _planned()

TECHNOLOGY_KINDS = frozenset(
    {"technology", "language", "framework", "library", "ai_model", "database", "platform", "api"}
)
COMPANY_KINDS = frozenset({"company", "organization"})

OPERATIONS: dict[str, Route] = {route.operation_id: route for route in ROUTES}
if len(OPERATIONS) != len(ROUTES):  # pragma: no cover - guards import time
    raise RuntimeError("duplicate operation ids in the route registry")


def find_route(method: str, path: str) -> tuple[Route, dict[str, str]] | None:
    """The route and raw path parameters for an exact method and path, if any."""
    for route in ROUTES:
        if route.method == method:
            params = route.match(path)
            if params is not None:
                return route, params
    return None


def methods_for_path(path: str) -> set[str]:
    return {route.method for route in ROUTES if route.match(path) is not None}


def describe(route: Route) -> dict[str, Any]:
    """A plain summary used by docs and tests."""
    return {
        "operation_id": route.operation_id,
        "method": route.method,
        "path": route.path,
        "status": route.status.value,
    }
