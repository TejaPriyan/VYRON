# VYRON: The Living Map of Technology

Explore how the digital world connects, evolves, and changes.

VYRON is an open-source, provenance-first interactive atlas of the technology ecosystem: technologies, languages,
frameworks, AI models, companies, open-source projects, repositories, and packages — with shortest-path tracing,
timelines, live GitHub repository analysis, live PyPI/npm dependency graphs, release histories, and side-by-side
comparisons. The graph, data, and deterministic analysis work on their own; AI is an optional future layer on top,
never the source of truth. Every fact carries a provenance label, and nothing is shown as sourced without evidence.

## Status: early development, not production ready

**The Next.js web app and database persistence adapters do not exist yet.** What runs today is a verified,
standard-library Python HTTP API (`vyron_api`, 21 of 34 routes implemented) paired with a dependency-free
**four-page browser prototype** (plain HTML, generated design tokens, and `tsc`-compiled TypeScript with zero npm
runtime packages and a strict Content Security Policy):

| Page | Path | What it does | Data source |
| --- | --- | --- | --- |
| **Map & Timeline** | `/` | 60 entities, 68 relationships, 20 auto-detected clusters, fuzzy search, accessible outline, **Shortest Path Finder** (`GET /api/v1/graph/path`), historical year slider with **Play/Pause playback**, and JSON entity export. | Development seed (`provenance: "seed"`, labelled everywhere) |
| **Repository** | `/repository.html` | Reads any public GitHub repository live: stars, forks, open issues & PRs, license, language byte shares, cache freshness (`Live` / `Confirmed` / `Stale`), exact API `source_urls`, opt-in top contributors & latest release, opt-in 52-week commit activity, PyPI/npm lookup links, and JSON export. | Live GitHub REST API (`provenance: "sourced"`, unstored) |
| **Dependencies & Releases** | `/dependencies.html` | Reads one PyPI or npm package at a time, grows a layered dependency graph on demand, separates left-out conditional/dev/peer/optional declarations, renders an opt-in **Release History** strip & table, cross-links GitHub homepages, and exports JSON evidence packs. | Live PyPI & npm APIs (`provenance: "sourced"`, unstored) |
| **Side-by-Side Compare** | `/compare.html` | Compares two PyPI/npm packages (with Shared vs. Only-A vs. Only-B direct dependency diff and release spans) or two public GitHub repositories (counts, status, and language overlap), with provenance badges, exact source URLs, and JSON export. | Live GitHub / PyPI / npm APIs (`provenance: "sourced"`, unstored) |

Read [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for the exact record of what is verified versus
what remains UNVERIFIED (FastAPI ASGI binding, Next.js app, WebGL renderer, PostgreSQL/Neo4j/Redis adapters,
Gemini SDK adapter, authentication, Docker builds, and CI execution).

## SEO, AEO & GEO Discovery

All prototype pages ship with strict-CSP-safe metadata (Open Graph, Twitter Cards, `robots`, `theme-color`, semantic
landmarks including `<search>`) and machine-readable discovery endpoints served from the same origin:

- `/robots.txt` — Search crawler and AI answer/generative engine policy (`GPTBot`, `OAI-SearchBot`, `ClaudeBot`, `PerplexityBot`, `Google-Extended`)
- `/sitemap.xml` — XML sitemap covering all four pages and `/llms.txt`
- `/llms.txt` — Structured **GEO / AEO** manifest with citation-ready project summary, routes, API overview, and Q&A
- `/schema.jsonld` — External JSON-LD structured data (`WebSite`, `SoftwareApplication`, `WebPage`, `FAQPage`) linked via `<link rel="alternate" type="application/ld+json">` so CSP stays inline-script-free
- `/site.webmanifest` — Web App Manifest

## Try It Locally

Requirements: Python 3.12+ (standard library only) and TypeScript (`tsc` 5.8+, or `npx --package=typescript tsc`) to
compile the browser modules.

```bash
python3 scripts/build_prototype.py          # compiles 37 ES modules into apps/web/prototype/dist/js
python3 apps/web/prototype/serve.py         # serves http://127.0.0.1:8080/
```

Open:

- Map: `http://127.0.0.1:8080/`
- Repository: `http://127.0.0.1:8080/repository.html`
- Dependencies & Releases: `http://127.0.0.1:8080/dependencies.html`
- Compare: `http://127.0.0.1:8080/compare.html`

Optional server flags: `--port 0` (pick a free port), `--no-github`, `--no-registries`, `--no-rate-limit`. Set
`GITHUB_TOKEN` (no scopes needed for public repos) to raise GitHub's rate limit from 60 to 5,000 requests/hour.
The local server refuses `GITHUB_TOKEN` on non-loopback addresses unless `--allow-token-on-network` is passed.

### Live Verification Scripts

```bash
python3 scripts/live_github_check.py                     # check real GitHub API
python3 scripts/capture_registry.py                      # check real PyPI & npm dependencies + release dates
python3 scripts/standin_github.py --mode prototype       # offline fixture stand-in for GitHub
python3 scripts/standin_registry.py --mode prototype     # offline fixture stand-in for PyPI & npm
```

## Preview Deployment on Vercel (UNVERIFIED on Cloud)

This repository includes [`vercel.json`](vercel.json), [`.vercelignore`](.vercelignore), and a Python WSGI preview
entrypoint ([`api/index.py`](api/index.py)) that builds the prototype modules with `tsc` and routes requests through
`vyron_api` + the prototype static table.

- **Verified locally**: WSGI contract tests confirm `api/index.py` serves all pages, SEO/AEO/GEO files, and API routes.
- **UNVERIFIED on Vercel**: It has not yet been deployed to Vercel's cloud runtime, and it is **not production ready**
  (seed graph only, no persistent database, per-instance in-memory caches and rate limits).
- Optional Vercel Environment Variables: `GITHUB_TOKEN` (public read quota), `VYRON_ENABLE_GITHUB` (`true`/`false`),
  `VYRON_ENABLE_REGISTRIES` (`true`/`false`). Never commit `.env` or tokens.

## Running the Test Suite

```bash
bash scripts/verify_stdlib.sh            # Python compile, API tests, root/contract tests, Node TS tests, tsc, emit, generators
bash scripts/verify_stdlib.sh --strict   # fail if any optional tool (docker, etc.) is missing
```

## Repository Layout

```text
api/              Vercel Python WSGI preview entrypoint (api/index.py)
apps/api          Python backend (vyron_api): domain, graph, GitHub, PyPI/npm, HTTP API, SQL tooling
apps/web          Design tokens and generated CSS; apps/web/prototype is the 4-page browser prototype
packages/types    Generated TypeScript API types and route table
packages/api-client  Typed zero-dependency fetch client
packages/graph    Pure map, timeline, dependency-graph, and release-strip geometry
packages/ui       Generated design tokens, error states, and empty-state copy
infrastructure    PostgreSQL migrations (0001..0005), seed SQL, Docker lint rules
scripts           Code generators, build_prototype.py, verify_stdlib.sh, live checks, stand-ins
tests             Cross-package tests: design, TS integration, Docker, prototype contracts, hygiene
services/         Reserved placeholders for future background workers
```

## Documentation & Community

| Document | Coverage |
| --- | --- |
| [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) | Verified vs. UNVERIFIED status record |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design and architectural decisions |
| [docs/API.md](docs/API.md) | Generated HTTP contract & route table ([docs/openapi.json](docs/openapi.json)) |
| [docs/PROTOTYPE.md](docs/PROTOTYPE.md) | Browser prototype architecture and screenshots ([docs/prototype/](docs/prototype/)) |
| [docs/GITHUB.md](docs/GITHUB.md) | GitHub integration, caching, rate limits, and live checks |
| [docs/REGISTRIES.md](docs/REGISTRIES.md) | PyPI & npm dependency and release-date readers |
| [docs/DATABASE.md](docs/DATABASE.md) | SQL schema, migrations, and integrity views |
| [docs/SECURITY.md](docs/SECURITY.md) | Implemented and planned security controls (see also [SECURITY.md](SECURITY.md)) |
| [docs/FRONTEND.md](docs/FRONTEND.md) | Design system, tokens, and future Next.js architecture |
| [docs/COMPONENTS.md](docs/COMPONENTS.md) | Component specifications |
| [docs/AI.md](docs/AI.md) | AI research layer and citation validation rules |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Local server, Docker Compose, and deployment gate |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Contribution guidelines and conventions |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community standards |

## License

Licensed under the [MIT License](LICENSE).
