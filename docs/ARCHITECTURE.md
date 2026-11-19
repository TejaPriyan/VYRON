# VYRON Architecture

Status: a design document, written first and revised as parts were built. Items are marked
**Decided**, or **To verify** where the decision depends on documentation or behavior that has not yet
been confirmed against a real install. This describes the intended system: much of it is not built.
Nothing here claims an integration works until it has been run; what exists and what has been run is in
[IMPLEMENTATION_STATUS.md](./IMPLEMENTATION_STATUS.md). Detail lives in [DATABASE.md](./DATABASE.md),
[GITHUB.md](./GITHUB.md), [AI.md](./AI.md), [SECURITY.md](./SECURITY.md), [API.md](./API.md) and
[DEPLOYMENT.md](./DEPLOYMENT.md).

## 1. Starting point

The repository was empty (no source, manifests, configuration, database setup or tests), so nothing
was restructured or removed. Toolchain in the build environment: Node 22, pnpm 10, Python 3.13, uv,
Docker. Current docs consulted on 2026-10-05: GitHub REST rate limits and best practices, Next.js
installation (16.x, Node 20.9+), Gemini models and structured output. Later, the package registries
turned out to be unreachable from the build environment, which is why much of this is designed rather
than built and run.

## 2. System overview

```
Browser
  |  HTTPS
Next.js (apps/web, Vercel)  -- server components + route handlers act as the BFF
  |  server-to-server only; no provider secrets, no direct browser -> API calls with secrets
FastAPI (apps/api)  ----------------------------------------------+
  |        |          |                                           |
PostgreSQL  Neo4j    Redis                          GitHub API   Gemini   research sources
(authority) (graph   (cache, rate limits, queue)    (provider)  (provider) (provider)
             projection)         ^
                                 |
                  Workers (services/*): ingestion, github, research, analytics
```

**Decided.** Workers run the same `vyron_api` package with different entrypoints (one image, no
duplicated domain code). `services/*` holds job handlers and entrypoints, not a second domain model.

## 3. Service boundaries

| Component | Owns | Must not |
| --- | --- | --- |
| `apps/web` | UI, rendering, BFF route handlers, session cookie forwarding | hold GitHub/Gemini keys; call providers |
| `apps/api` | domain logic, `/api/v1`, auth, rate limiting, provider adapters | render UI |
| workers | ingestion, projection, scheduled refresh, long research runs | serve user requests |
| `packages/*` | shared types, graph layout/LOD code, UI primitives, config | contain secrets |

Provider interfaces (`apps/api/src/vyron_api/providers/`): `AIProvider`, `GitHubProvider`,
`ResearchProvider`, `GraphRepository`, `EntityRepository`, `CacheProvider`. Application code depends
on these protocols only, so Gemini or any other backend can be replaced without touching callers.

## 4. Data authority

| Data | Authority | Notes |
| --- | --- | --- |
| Entities, attributes, relationships, sources, evidence | PostgreSQL | relationships carry provenance and evidence ids |
| Users, sessions, bookmarks, saved maps, search history | PostgreSQL | |
| Jobs, research runs, API usage | PostgreSQL | the job row is the durable record; the queue is transient |
| Graph traversal structure | Neo4j (derived) | holds id, kind, slug, name and typed edges only; rebuildable from PostgreSQL by a projector |
| Cache, rate-limit counters, queue | Redis | everything in Redis is losable |

Neo4j is never written by user-facing request code, only by the projector job. Entity detail pages
read PostgreSQL; traversal, neighborhood and path queries read Neo4j. Fields are not duplicated into
Neo4j beyond what traversal and layout need.

## 5. Provenance and evidence (trust layer)

Every stored fact carries a `provenance` label (`domain/provenance.py`): `sourced`, `estimated`,
`inferred`, `predicted`, `user_generated`, `ai_generated`, `seed`. A `sourced` fact must reference at
least one evidence row, enforced in the domain model and later by a database constraint. Evidence
links claim -> source -> entity -> timestamp. Sources hold URL, title, publisher, publication date,
retrieval date, type and reliability metadata. URLs are only ever stored if a provider returned
them; the system never synthesizes a citation. Seed data is labelled `seed` and displayed as
development data.

Technology DNA: a metric is shown only if it has a registered, versioned formula and recorded
inputs, and the UI exposes the methodology. Software Genome metrics are shown only if actually
measured from ingested data.

## 6. Data flows

**Ingestion.** Request -> job row (status, source, entity, start/end, error, retry count) -> queue ->
worker -> `GitHubProvider` (serial/low-concurrency queue, conditional requests) -> normalize ->
upsert entities, sources, evidence -> projector updates Neo4j -> cache invalidation. Ingestion is
controlled (specific repositories, organizations, curated lists, scheduled and manual refresh); it
does not crawl GitHub at large.

**Read path.** Web -> API -> Redis -> PostgreSQL or Neo4j. Responses are paginated; the browser
never receives a whole graph.

**Research.** Request -> orchestrator -> retriever (VYRON graph + PostgreSQL + external sources) ->
evidence collector -> AI provider -> structured result -> evidence validation -> response, with the
run stored (query, retrieved entities, sources, model, timestamp, response, evidence mappings,
confidence metadata). Validation rejects any citation id that is not in the retrieved evidence set;
statements without evidence are labelled `ai_generated` or `inferred`, never presented as sourced.
Deterministic code is preferred for SCOUT, HISTORIAN, GRAPH ANALYST, EVIDENCE ANALYST, TREND
ANALYST and CRITIC; the model is used only where generation adds value.

## 7. Graph engine and global map

The server precomputes cluster structure and layout and serves level-of-detail slices: clusters when
zoomed out, individual entities when zoomed in, with viewport-bounded queries and payload limits.
The client renders with WebGL (Three.js / React Three Fiber) and uses D3 for 2D charts.
**To verify (Phase 7):** benchmark against 10k+ nodes and fall back to canvas rendering if WebGL
cannot meet the budget. The map must have a non-visual alternative (an accessible list/tree of the
same neighborhood). Digital City is a rendering mode over the same graph, with no separate data
model.

## 8. GitHub integration

Documented behavior (GitHub docs, checked 2026-10-05; re-checked by `scripts/github_304_probe.py` on
2026-10-28): 60 requests/hour unauthenticated, 5,000/hour authenticated; state in
`x-ratelimit-limit|remaining|used|reset|resource`; unauthenticated `304 Not Modified` responses were
measured to cost 1 request each (authenticated 304s remain unverified; see [GITHUB.md](./GITHUB.md));
secondary limits include a concurrency ceiling and
per-minute point budgets; on 403/429 honor `retry-after`, else wait for `x-ratelimit-reset` when
exhausted, else at least one minute with exponential backoff; `GET /rate_limit` reports status
without consuming quota; use pagination links, not hand-built URLs.

Implemented and unit-tested: `github/ratelimit.py` (header parsing, retry decisions that never retry
a plain 403), `github/url.py` (strict repository reference parsing), and, composed from those and the
transport, cache and budget modules, `github/provider.py`, the **live repository reader** behind
`GET /api/v1/github/repositories/{owner}/{repo}` and `GET /api/v1/github/rate-limit` (ETag/Last-Modified
caching with a `fresh`, `revalidated` or `stale` status and a read time on every answer). It has been run
end to end only against a local stand-in with fixture data; **against the real GitHub it is UNVERIFIED**
until `scripts/live_github_check.py` passes there. **To build (Phase 5):** a low-concurrency request queue
and the ingestion jobs, a Redis cache shared across instances, and the commit-activity and dependency reads,
which should be written against captured real responses. (Top contributors and the latest release exist as an
opt-in read, seen once against the real GitHub for one repository on 2026-11-02; its 204, 403 and 404 answers are
written from the documentation only and UNVERIFIED.) The token lives only in server environment
variables. Details: [GITHUB.md](./GITHUB.md).

## 9. AI architecture

`AIProvider` takes a request (system, prompt, optional JSON schema) and returns text plus model and
provider names. The Gemini adapter lives behind it. **To verify:** the Gemini docs reviewed show the
`google-genai` SDK, and the structured-output page uses a newer `interactions` API alongside the
older `generate_content` surface. The adapter will be written against whichever surface is
confirmed by inspecting the installed SDK, and the model id comes from configuration (the models page
listed `gemini-3.8-flash` as stable on 2026-10-05; confirm before use). If no key is configured or
the provider fails, AI features are disabled gracefully and everything else keeps working.

Prompt injection: retrieved GitHub and web content is passed to the model as clearly delimited data,
never as instructions; model output is parsed and validated as JSON against a schema; nothing the
model returns is ever executed.

## 10. Authentication and sessions

**Decided.** Argon2id password hashing; opaque server-side sessions stored in PostgreSQL; cookie is
`HttpOnly`, `Secure`, `SameSite=Lax`; CSRF defense by Origin/Host verification plus a token on
state-changing requests; auth endpoints rate limited per IP and per account through Redis; all input
validated with typed schemas; responses never include password hashes or session identifiers.

## 11. Caching

| Layer | Key | TTL idea | On failure |
| --- | --- | --- | --- |
| GitHub responses | repo key + ETag | hours for stable data | serve stale |
| Graph neighborhoods | entity id + depth + filters | minutes | fall back to PostgreSQL |
| Search | normalized query + filters | minutes | skip cache |
| AI results | stored research runs | durable | n/a |

If Redis is unavailable, `InMemoryTTLCache` provides a per-process fallback.

## 12. Security

Secrets only in environment variables; `.env` ignored by git; no provider keys in any browser bundle;
all provider calls server-side. User-supplied URLs are never fetched: the only accepted form is a
GitHub repository reference parsed strictly into `owner/repo` (github.com only, no credentials,
ports, encodings or odd characters). Any future outbound fetch must use a host allowlist and
resolved-address checks. Implemented and unit-tested in the HTTP application: request size limits,
secure headers, strict input validation, per-bucket rate limits (in-process), signed cursors. Still
planned: HTML sanitization in the web app, parameterized queries only (no database adapter exists),
dependency auditing in CI, authentication, and redaction of secrets in logs. See
[SECURITY.md](./SECURITY.md) for the full split.

## 13. Observability

Structured JSON logs with a request id, plus counters and latency for request errors, API latency,
database and external API errors, ingestion failures, AI failures, rate-limit events and job
failures. A redaction step removes passwords, tokens and keys before anything is logged.

## 14. Degradation

| Failure | Behavior |
| --- | --- |
| GitHub unavailable | serve cached data with a staleness notice; new ingestion jobs retry with backoff |
| Gemini unavailable or unconfigured | AI panels show an unavailable state; graph, timeline, repository analysis still work |
| Neo4j unavailable | bounded-depth neighborhoods from PostgreSQL; path-finding and global layout marked unavailable |
| Redis unavailable | in-process cache, rate limits fall back to per-process counters, jobs paused |

No screen may render blank; every failure maps to a message and a retry or alternative.

## 15. Testing

Unit: graph algorithms, parsers, validation, scoring, GitHub normalization, AI response parsing
(written and run, standard library only). Contract: generated OpenAPI, TypeScript types and design tokens
are checked for freshness, and the TypeScript client runs against the real API over localhost (written
and run). Integration still to write: PostgreSQL, Neo4j, GitHub adapter (recorded fixtures plus a gated
live test), auth.
End-to-end: sign in, search, open entity, explore graph, analyze repository, bookmark, launch
research, view timeline. Error cases: missing keys, invalid repositories, malformed external data,
rate limits, AI failure, database failure, empty results.

## 16. Deployment

Web on Vercel. API and workers as one container image on any container host. Managed PostgreSQL,
Neo4j (Aura or self-hosted) and managed Redis. **Decided:** migrations are plain SQL files applied by the
in-repository tool (`apps/api/src/vyron_api/db/migrations.py`): forward-only, checksum-verified, run as a
release step under an advisory lock before the new API version takes traffic. This replaced the earlier
idea of Alembic with SQLAlchemy, because the planner could be written and tested without a database or any
package. The PostgreSQL executor is not written (**UNVERIFIED**). See [DEPLOYMENT.md](./DEPLOYMENT.md).

## 17. Open decisions

- Job queue library: choose in Phase 5 after reading its current documentation.
- Map renderer: confirm by benchmark in Phase 7.
- Gemini SDK surface: confirm on install (blocked until package installs work in the build environment).
