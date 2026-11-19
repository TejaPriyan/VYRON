# VYRON API contract

Status of this document: **the contract and the stdlib-verified implementation described here are real
and tested; the production bindings are not.** See "What is verified" at the end.

- Machine-readable contract: [`openapi.json`](openapi.json) (OpenAPI 3.1, generated, never hand-edited).
- Source of truth: the route registry (`apps/api/src/vyron_api/api/routes.py`) and the schema
  components (`.../api/schemas.py`). OpenAPI, the HTTP application, query parsing and the route table
  below are all derived from them, and tests fail if any of them drift.
- Regenerate after changing the registry: `python3 scripts/generate_openapi.py` and
  `python3 scripts/generate_api_docs.py`.

## Conventions

**Versioning and transport.** Everything lives under `/api/v1/`, plus `/health/*` probes. Bodies are
JSON (UTF-8). The browser never calls this API directly: the Next.js server proxies it
(server-to-server), so there is no CORS configuration and no API key in any frontend bundle.
Unknown routes are `404`, wrong methods `405` with an `Allow` header, and operations that are specified
but not built answer `501 not_implemented`.

**Errors.** Every non-2xx response has the same shape, and messages never contain internals (SQL,
stack traces, upstream bodies, tokens):

```json
{"error": {"code": "validation_error", "message": "invalid query parameters",
           "request_id": "9f2c…", "details": [{"field": "limit", "issue": "must be <= 100"}]}}
```

| Code | HTTP | Meaning |
|---|---|---|
| `bad_request` | 400 | Malformed request. |
| `unauthenticated` | 401 | Session required (planned). |
| `forbidden` | 403 | Not allowed (planned). |
| `not_found` | 404 | Unknown route or resource. |
| `method_not_allowed` | 405 | See the `Allow` header. |
| `conflict` | 409 | The request conflicts with the data model's rules. |
| `payload_too_large` | 413 | Body over 64 KiB. |
| `unsupported_media_type` | 415 | Reserved for body endpoints. |
| `validation_error` | 422 | A parameter or field is invalid; `details` lists every problem. |
| `rate_limited` | 429 | Too many requests; honour `Retry-After`. |
| `internal_error` | 500 | Unexpected; quote `request_id` when reporting. |
| `not_implemented` | 501 | Specified, not built yet. |
| `upstream_error` | 502 | A provider (for example GitHub) is failing; not the caller's fault. |
| `service_unavailable` | 503 | A backing service is down; retry later. |
| `upstream_rate_limited` | 503 | A provider's rate limit is exhausted; `Retry-After` says when to retry. |

`request_id` is echoed in the `X-Request-ID` header. A client-supplied `X-Request-ID` is honoured only
if it matches `[A-Za-z0-9._-]{8,64}`, otherwise a fresh one is generated (header-injection safe).

**Validation is strict.** Unknown query parameters are rejected, scalar parameters may not repeat,
integers are ASCII digits only, booleans are exactly `true` or `false`, control characters are refused,
and all problems are reported together.

**Pagination.** Collections take `limit` (1–100, default 25) and `cursor`, and return
`page: {limit, total, next_cursor}`; `next_cursor` is `null` on the last page. Cursors are opaque,
HMAC-signed, expire after 24 hours and are bound to the operation and its filters, so a cursor from one
query is refused (`422`, field `cursor`) on another. Paging is offset-based: if rows are inserted
between page requests a client can see a duplicate or miss an item. Nothing returns an unbounded list;
neighbourhoods are capped at 300 nodes and 1,000 relationships and say `truncated: true` when cut.

**Caching.** Public data endpoints send `Cache-Control: public, max-age=N` and an `ETag`; send it back
as `If-None-Match` for `304`. Errors, health probes and anything authenticated are `no-store`.

**Rate limiting.** Per client address and per bucket (`health`, `read`, `search`, `graph`, `github`,
`auth`, `ai`, `write`). Responses carry `RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset`;
a `429` adds `Retry-After`. Credential endpoints get the strictest bucket. `X-Forwarded-For` is ignored
unless `TRUSTED_PROXY_HOPS` says how many trusted proxies are in front.

**Security headers** on every response, including errors: `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`,
`Cross-Origin-Resource-Policy: same-site`, `Strict-Transport-Security`, `X-Frame-Options: DENY`.

## Provenance and development data

Every entity, relationship and timeline event carries a `provenance` label: `sourced`, `estimated`,
`inferred`, `predicted`, `user_generated`, `ai_generated` or `seed`. Only `sourced` requires evidence.
The API never presents anything else as a verified fact, and:

- Any response containing seed data has a non-null `data_notice` (and `/api/v1/meta` reports
  `data_mode: "seed"`). Clients must show it.
- Predictions (`predicted`, `is_prediction: true`) are excluded from timelines unless
  `include_predictions=true`.
- Graph scores (`graph/overview`) are PageRank estimates and say so (`score_provenance: "estimated"`).
- AI output (planned) can only be `ai_generated` unless it cites evidence the run was actually shown.

## Route reference

<!-- BEGIN GENERATED: route reference (scripts/generate_api_docs.py) -->

### Implemented (21)

| Operation | Summary | Parameters (`*` = required) | Request body | Response | Auth | Rate bucket | Cache | Status |
|---|---|---|---|---|---|---|---|---|
| `GET` `/health/live` | Liveness probe | - | - | 200 Health | public | health | no-store | implemented |
| `GET` `/health/ready` | Readiness probe | - | - | 200 Health | public | health | no-store | implemented |
| `GET` `/api/v1/meta` | Service metadata, vocabularies and limits | - | - | 200 Meta | public | read | 300s | implemented |
| `GET` `/api/v1/entities` | List entities by name | `kind` (query: EntityKind[] (repeat))<br>`limit` (query: integer, 1..100, default 25)<br>`cursor` (query: string, ≤512 chars) | - | 200 EntityList | public | read | 30s | implemented |
| `GET` `/api/v1/entities/{entity_id}` | Get one entity | `entity_id`* (path: string, uuid) | - | 200 Entity | public | read | 30s | implemented |
| `GET` `/api/v1/entities/by-slug/{kind}/{slug}` | Get one entity by kind and slug | `kind`* (path: EntityKind)<br>`slug`* (path: string, ≤100 chars) | - | 200 Entity | public | read | 30s | implemented |
| `GET` `/api/v1/entities/{entity_id}/neighbors` | Entities connected to this one | `entity_id`* (path: string, uuid)<br>`depth` (query: integer, 0..3, default 1)<br>`relation_type` (query: RelationType[] (repeat))<br>`max_nodes` (query: integer, 1..300, default 100) | - | 200 Subgraph | public | graph | 30s | implemented |
| `GET` `/api/v1/entities/{entity_id}/timeline` | Timeline events for one entity | `entity_id`* (path: string, uuid)<br>`include_predictions` (query: boolean, default false)<br>`limit` (query: integer, 1..100, default 25)<br>`cursor` (query: string, ≤512 chars) | - | 200 TimelineList | public | read | 30s | implemented |
| `GET` `/api/v1/technologies` | List technology-like entities | `kind` (query: EntityKind[] (repeat))<br>`limit` (query: integer, 1..100, default 25)<br>`cursor` (query: string, ≤512 chars) | - | 200 EntityList | public | read | 30s | implemented |
| `GET` `/api/v1/companies` | List companies and organizations | `kind` (query: EntityKind[] (repeat))<br>`limit` (query: integer, 1..100, default 25)<br>`cursor` (query: string, ≤512 chars) | - | 200 EntityList | public | read | 30s | implemented |
| `GET` `/api/v1/search` | Ranked entity search | `q`* (query: string, ≤100 chars)<br>`kind` (query: EntityKind[] (repeat))<br>`limit` (query: integer, 1..100, default 25)<br>`cursor` (query: string, ≤512 chars) | - | 200 EntityList | public | search | 30s | implemented |
| `GET` `/api/v1/graph/overview` | Whole-graph statistics, central entities and clusters | `top` (query: integer, 1..20, default 10)<br>`clusters` (query: integer, 1..20, default 8) | - | 200 GraphOverview | public | graph | 60s | implemented |
| `GET` `/api/v1/graph/path` | Shortest connection between two entities | `from`* (query: string, uuid)<br>`to`* (query: string, uuid)<br>`max_depth` (query: integer, 1..8, default 6) | - | 200 PathResult | public | graph | 30s | implemented |
| `GET` `/api/v1/timeline` | Timeline events across entities | `start_year` (query: integer, 1900..2200)<br>`end_year` (query: integer, 1900..2200)<br>`include_predictions` (query: boolean, default false)<br>`limit` (query: integer, 1..100, default 25)<br>`cursor` (query: string, ≤512 chars) | - | 200 TimelineList | public | read | 30s | implemented |
| `GET` `/api/v1/github/parse` | Classify a pasted GitHub link without contacting GitHub | `input`* (query: string, ≤300 chars) | - | 200 GitHubTarget | public | github | no-store | implemented |
| `GET` `/api/v1/github/repositories/{owner}/{repo}` | Analyze a public GitHub repository | `owner`* (path: string, ≤39 chars)<br>`repo`* (path: string, ≤100 chars) | - | 200 RepositoryAnalysis | public | github | 60s | implemented |
| `GET` `/api/v1/github/repositories/{owner}/{repo}/activity` | Top contributors and latest release of a public GitHub repository | `owner`* (path: string, ≤39 chars)<br>`repo`* (path: string, ≤100 chars) | - | 200 RepositoryActivity | public | github | 60s | implemented |
| `GET` `/api/v1/github/repositories/{owner}/{repo}/commit-activity` | A year of weekly commit counts for a public GitHub repository | `owner`* (path: string, ≤39 chars)<br>`repo`* (path: string, ≤100 chars) | - | 200 CommitActivity | public | github | no-store | implemented |
| `GET` `/api/v1/registries/{registry}/package` | A package's declared dependencies, read from PyPI or npm | `registry`* (path: string)<br>`name`* (query: string, ≤214 chars) | - | 200 PackageReport | public | registry | 60s | implemented |
| `GET` `/api/v1/registries/{registry}/releases` | A package's release dates, read from PyPI or npm | `registry`* (path: string)<br>`name`* (query: string, ≤214 chars) | - | 200 PackageReleases | public | registry | 60s | implemented |
| `GET` `/api/v1/github/rate-limit` | The server's current GitHub API quota | - | - | 200 RateLimitStatus | public | github | no-store | implemented |

### Planned (answer 501 today) (13)

| Operation | Summary | Parameters (`*` = required) | Request body | Response | Auth | Rate bucket | Cache | Status |
|---|---|---|---|---|---|---|---|---|
| `POST` `/api/v1/auth/register` | Create an account | - | Credentials | 201 UserProfile | public | auth | no-store | **planned** |
| `POST` `/api/v1/auth/login` | Start a session | - | Credentials | 200 UserProfile | public | auth | no-store | **planned** |
| `POST` `/api/v1/auth/logout` | End the session | - | - | 204 (empty) | session | auth | no-store | **planned** |
| `GET` `/api/v1/user/me` | The signed-in user's profile | - | - | 200 UserProfile | session | read | no-store | **planned** |
| `GET` `/api/v1/entities/{entity_id}/evidence` | Evidence and sources behind an entity's statements | `entity_id`* (path: string, uuid)<br>`limit` (query: integer, 1..100, default 25)<br>`cursor` (query: string, ≤512 chars) | - | 200 EvidenceList | public | read | 30s | **planned** |
| `POST` `/api/v1/research` | Start an AI-assisted research run | - | ResearchRequest | 202 ResearchRun | session | ai | no-store | **planned** |
| `GET` `/api/v1/research/{run_id}` | Read a research run | `run_id`* (path: string, uuid) | - | 200 ResearchRun | session | read | no-store | **planned** |
| `POST` `/api/v1/future/scenarios` | Start a Future Lab scenario (always labelled 'predicted') | - | ResearchRequest | 202 ResearchRun | session | ai | no-store | **planned** |
| `GET` `/api/v1/user/bookmarks` | List bookmarked entities | `limit` (query: integer, 1..100, default 25)<br>`cursor` (query: string, ≤512 chars) | - | 200 BookmarkList | session | read | no-store | **planned** |
| `PUT` `/api/v1/user/bookmarks/{entity_id}` | Bookmark an entity | `entity_id`* (path: string, uuid) | - | 200 Bookmark | session | write | no-store | **planned** |
| `DELETE` `/api/v1/user/bookmarks/{entity_id}` | Remove a bookmark | `entity_id`* (path: string, uuid) | - | 204 (empty) | session | write | no-store | **planned** |
| `GET` `/api/v1/user/maps` | List saved maps | `limit` (query: integer, 1..100, default 25)<br>`cursor` (query: string, ≤512 chars) | - | 200 SavedMapList | session | read | no-store | **planned** |
| `POST` `/api/v1/user/maps` | Save a map | - | SavedMapInput | 201 SavedMap | session | write | no-store | **planned** |

<!-- END GENERATED -->

## Design decisions

- **Registry-first, not model-first.** The contract is data (JSON Schema dicts), so the same schemas
  generate OpenAPI, parse query parameters and validate responses in tests. FastAPI is used only as an
  ASGI host (`fastapi_app.py`); if Pydantic models are preferred later they can be generated from
  `openapi.json` and cross-checked in CI.
- **Framework-independent core.** `Application.handle(Request) -> Response` holds all HTTP behaviour,
  so it is tested without a framework and can be bound to WSGI (verified) or ASGI (not yet verified).
- **Fail closed.** An authenticated route accidentally marked implemented answers `401` for everyone;
  an unknown `data_mode` or a production config without a stable secret refuses to start.
- **The repositories are protocols.** `CatalogService` knows only `EntityRepository`,
  `GraphRepository`, `GraphAnalytics` and `TimelineRepository`; the in-memory seed adapter is the
  reference the PostgreSQL/Neo4j adapters must match.

## What is verified

| Part | Status |
|---|---|
| Registry, schemas, validator, parameter parsing, cursors, rate limiter, error mapping | VERIFIED by stdlib tests |
| HTTP application, all implemented operations, response-vs-schema checks | VERIFIED in-process and over real sockets (stdlib WSGI server) |
| OpenAPI 3.1 document | VERIFIED structurally (refs resolve, ids unique, enums match domain and SQL); NOT validated by an external OpenAPI validator |
| `fastapi_app.py` | **UNVERIFIED**: FastAPI cannot be installed here; only parse/import-name checks run |
| PostgreSQL/Neo4j/Redis adapters, auth sessions, GitHub and AI routes | **NOT WRITTEN** (routes are specified and answer 501) |
