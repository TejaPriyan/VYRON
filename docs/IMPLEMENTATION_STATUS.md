# Implementation status

Last updated: 2026-11-19. This file records what exists and what has actually been run.

- **Written** means the file exists. **Verified** means a command was run and its result observed.
- **UNVERIFIED** means it was written without being able to run it (usually because it needs a package
  or a service that is not available here). UNVERIFIED code may be wrong in ways nothing has tested.
- VYRON is not production ready and nothing in this repository claims it is. That claim waits until
  the dependencies can be installed and the whole application has been run, tested and built.
- **Reports from the owner's machine** are labelled "reported". Files they send (captures, screenshots) are what
  they are and I read them. A pasted command listing is weaker: on 2026-11-02 one claimed to be complete and
  unedited but named TypeScript tests that exist in no copy of this repository that I have, and showed about 100 of the
  228 tests. Such a listing counts only through its summary lines (pass and fail counts, "up to date"), which matched my
  own runs; the detail is not relied on.

## In one paragraph

What exists today is the **core that can be checked without installing anything**: the domain model,
provenance and evidence rules, an in-memory graph with algorithms and search, GitHub URL parsing, a
**live GitHub repository reader** (provider, cache, rate-limit and retry logic, four API routes, two of them opt-in reads: top contributors with the latest release, and a year of weekly commit activity), PostgreSQL
migrations (statically linted, never run), a framework-independent HTTP application with a generated
OpenAPI contract, a typed TypeScript client verified against the real Python server, frontend design tokens
computed and tested for contrast, graph-view helpers, and Docker and CI configuration. On top of that sits a
**dependency-free browser prototype** ([PROTOTYPE.md](PROTOTYPE.md)): plain HTML and `tsc`-compiled
TypeScript served by a standard-library server next to the real API. Its map page shows the 60 seed
entities, 68 relationships and 20 clusters with search, details, a text outline, keyboard and touch use, and a
timeline strip that shows the map as of a chosen year (seed dates only);
its **repository page** reads one public GitHub repository when asked and shows what GitHub said, where each
figure came from and how old it is, and offers contributors, the latest release and a year of commit activity on request;
its **dependencies page** (2026-11-14) reads one PyPI or npm package at a time through a new API route, with the source
and age of each answer, and draws a dependency graph that grows when a package is opened (a local stand-in registry of
fixture data lets it run without internet, and `scripts/capture_registry.py` is the check against the real
registries, which the project owner reported passing on 2026-11-15, see below); under the graph it shows a package's **release
history** (2026-11-19): the dates PyPI and npm themselves record, as a strip and a table, with what each date means and what was
left out said in words (the route behind it has **not** been run against the real registries). All three pages were run in Chromium (462 checks, 48 screenshots in `docs/prototype/`), against local
stand-ins for GitHub and for PyPI and npm serving fixture data, because the build environment cannot reach either. **On 2026-10-26 the project owner reported one passing run of
`scripts/live_github_check.py` against the real GitHub (12 of 12 checks, one repository, no token), and on
2026-11-02 a second one (27 of 27) that also read contributors and the latest release for the same repository;
I did not see either. On 2026-11-04 he ran the commit-activity capture, which I did read: GitHub answered 202 three times and
then 200 for `pallets/flask`, and 200 at once for `torvalds/linux`; the commit-activity read was written from those answers. Everything those runs did not touch is still UNVERIFIED against GitHub** (see [GITHUB.md](GITHUB.md)). **The package route was reported passing against the real PyPI and npm on 2026-11-15 (10 of 10, one run, relayed: I did not see
it); the dependencies page has not been seen showing a real answer, and the release dates have never been read from the
real registries at all**
([REGISTRIES.md](REGISTRIES.md)). What
does **not** exist: the Next.js app and its other screens, the WebGL map, the PostgreSQL/Neo4j/Redis
adapters, GitHub ingestion, dependency analysis of a repository's manifests, the Gemini adapter,
authentication, and any real (non-seed) graph data. The prototype is a review aid, not the product, and was
only seen in one browser at 60 entities.

## Blocker: package registries are unreachable

`pypi.org` and `registry.npmjs.org` are refused by the sandbox proxy, and the request was explicit: do
not bypass it. Nothing was worked around, no package was installed, and no proxy or TLS setting was
changed. Consequences:

- `pytest` has never run on this code. `ruff` 0.16.9 and `mypy` 2.3.1 turned out to be installed in the sandbox and were
  run on 2026-11-14 (not as the project's locked toolchain: no `uv sync`, no pydantic plugin, no FastAPI), see register
  item 1.
- The FastAPI binding, the Next.js app, React, Three.js and D3 code could not be imported or built. The
  browser prototype exists because it needs none of them.
- The database drivers (PostgreSQL, Neo4j, Redis) and the Gemini SDK could not be inspected, so no
  adapter for them was written against a guessed interface.
- The Docker daemon is not running, so no image was built and no container was started.
- The GitHub gateway answers 403 "No linked GitHub account", so no live GitHub request has been made from here.
  The live repository reader was therefore written and run against `scripts/standin_github.py` (fixture data),
  and the project owner then ran `scripts/live_github_check.py` where GitHub is reachable (see the verification
  record).

To resume: allow `pypi.org`, `files.pythonhosted.org` and `registry.npmjs.org` (or provide a mirror),
then follow "Next steps" below.

## Verification record (2026-10-25)

Environment: Python 3.13.16, Node 22.22.0, TypeScript 6.0.3, Docker CLI 29.8.2 with Compose 5.5.1 and no
daemon, Chromium 141.0.7390.37 driven by the Python `playwright` 1.56.0 package (tooling that was already
installed here, not a project dependency). Everything except the Chromium row is run together by
`scripts/verify_stdlib.sh --strict`.

| Check | Command | Result |
| --- | --- | --- |
| API unit tests | `cd apps/api && PYTHONPATH=src python3 -m unittest discover -s tests` | 790 pass (includes 52 for the PyPI and npm readers and the command, 73 for the package and release routes, the reader's caches and dependency references, and 36 for release dates; 32 for the live GitHub provider, 53 for contributors and the latest release, 30 for commit activity, 8 for the Host-header allow-list, 35 on real captured GitHub responses, 3 for the SQLite adapter) |
| Root tests: design contract, TypeScript contract, Docker, repository hygiene, CI workflow structure, cross-language, prototype server and pages, GitHub stand-in, live-check script, 304 probe | `python3 -m unittest discover -s tests` | 399 pass (includes the 29 client-versus-real-API integration tests, 67 prototype server and page contract tests over all three pages (two of them guard the stylesheet's rule order), 51 tests of the PyPI and npm stand-in (38) and of `capture_registry.py`'s checks (13), 46 end-to-end tests of the live GitHub path against the stand-in, 64 tests of `live_github_check.py`, 15 tests of `github_304_probe.py`, 4 tests of the guard that every Python text read and write names its encoding, and runs of the Node suites) |
| TypeScript unit tests | `node --test packages/*/src/*.test.ts apps/web/prototype/src/*.test.ts` | 431 pass (graph 217 of which 47 are the dependency graph's and 26 the release strip's, api-client and ui 36, prototype page logic 178 of which 67 are the repository page's, 38 the dependencies page's, 30 the release history's wording and 19 the timeline strip's; the 7 prototype data tests against the live API run from the Python suite) |
| TypeScript strict type check | `tsc -p tsconfig.check.json` | clean |
| Browser prototype compiles | `python3 scripts/build_prototype.py` | emits 34 modules |
| Browser prototype in Chromium | `python3 scripts/verify_prototype_browser.py` (starts `serve.py`, the GitHub stand-in and the registry stand-in) | 462 checks pass: 134 on the map (60 entities) at desktop, medium and phone sizes, 39 of them on the timeline strip (the seed badge, one mark per dated entity, a year hiding exactly the later entities and dimming exactly the undated, the sentences, the address, selection and year interplay, folding, years outside the range, the dates failing and being retried) and the phone ones (opening level, two-row bar, tapping a region then a mark under the sheet, the strip folded and inert under the sheet), 138 on the repository page (healthy, bare, private, moved, missing, gateway, broken, garbled, flaky, rate-limited and switched-off cases, phone width, forced colours, and 39 on the opt-in contributors and latest-release section: asking nothing until pressed, the keyboard path, a list, none, declined, no release, a rate limit, a garbled list, a read abandoned mid-load, phone width, forced colours, and 34 on the opt-in commit-activity section: asking nothing until pressed, the keyboard path, "still computing" with focus on Check again and no automatic polling, the third ask drawing 52 bars, a year that never finishes, nothing to report, phone width, forced colours), and 190 on the dependencies page, 114 on the graph and 76 on the release history (idle, a root read, a graph grown one package at a time, a shared dependency, a cycle, a 40-dependency package, one that cannot be opened by name, a missing package, a registry that answers 500, 429 or garbage, an expansion that fails and is retried, an npm scoped package and a pasted link, phone and 320 px widths, and the registries switched off; for the release history: nothing asked until pressed, one request and no repeat, keyboard and pointer movement, the table and its cut at 100, npm, 272 releases with overlapping marks said in words, no dates, a 429 and a 500, a link-only dependency, phone and 320 px widths; **not** forced-colours mode, though the stylesheet has rules for it), all against **stand-in fixture data, not GitHub or PyPI or npm**; 48 screenshots in `docs/prototype/`, looked at by me. **Not part of `verify_stdlib.sh`; needs `playwright` and a Chromium build** |
| The live GitHub check script, run against the stand-in | `python3 scripts/live_github_check.py --base-url <stand-in>` (tests in `tests/test_live_github_check.py`) | 12 checks pass (27 with `--activity`, 21 with `--flow`) and the output says it is fixture data and the live path stays UNVERIFIED |
| The same script's `--flow` option: the page's own sequence (analyse, wait, load contributors and release, wait, load again) on a fresh cache, with each freshness label checked against what GitHub answered | `python3 scripts/live_github_check.py --flow` (tests in `tests/test_live_github_check.py`, 18 of its 64, waits simulated) | Passes against the stand-in, and **fails** when the provider is given the old label rule; 16 deliberate breakages of the script were each caught. **Run once against the real GitHub (reported 2026-11-03, `pallets/flask`, no token, output kept in `docs/evidence/`): 21 checks passed, exit 0**, which settled the "Confirmed" screenshot; one repository, one machine |
| The same script's `--capture DIR --stats` option: saves the last year of commit activity as GitHub sends it, asking again while the answer is 202 | `python3 scripts/live_github_check.py --capture captures_stats --stats --repo pallets/flask --repo torvalds/linux` (tests in `tests/test_live_github_check.py`, 12 of its 64, and 4 in `tests/test_standin_github.py`) | Passes against the stand-in; 21 deliberate breakages of the option and its fixture were each caught. **Run once against the real GitHub (2026-11-04, reported by the owner, files read by me):** `pallets/flask` 202, 202, 202, then 200 at 16.1 s; `torvalds/linux` 200 at once; each ask cost one request. The captures are now fixtures (`commit_activity_flask_linux`) and the stand-in answers in the same shape |
| Commit activity (`GET .../commit-activity`, the page section): never waits on GitHub, reports a 202 as "computing" and does not keep it, validates the weekly shape strictly | `apps/api/tests/test_github_commit_activity.py` (30), `test_github_real_captures.py` (8 of its 35), `repository-model.test.ts` (11 new), 34 Chromium checks, 5 client-versus-API integration tests | Pass; 13 deliberate breakages of the API code and 7 of the page's figures code were each caught. **Seen against the real GitHub only through the 2026-11-04 captures; the 204, a rate limit, a token and a real 304 for this endpoint are UNVERIFIED** (see [GITHUB.md](GITHUB.md), "Commit activity (opt-in)") |
| Contributors and the latest release (`GET .../activity`, the page section, `--activity`) | `apps/api/tests/test_github_activity.py` (53), `test_github_real_captures.py` (8 of its 27, plus 7 on the huge repository), `tests/test_standin_github.py` (14 of its 41), `tests/test_live_github_check.py` (16 of its 64), `packages/api-client/src/integration.test.ts` (5), 52 model tests, 39 Chromium checks; 21 deliberate one-line breakages of the code, all caught (two were missed at first, and two tests were added), and 6 more against the real-capture tests | **Seen once against the real GitHub** (reported by the owner, 2026-11-02, `pallets/flask`, 27 checks, exit 0, capture kept in `fixtures/github_real/pallets_flask_activity`): the healthy list and the latest release parsed and passed every check, at one request each. It differed from my hand-written fixtures in ways that did not change behaviour (a `Link` header, weak ETags and `Last-Modified`, extra fields, a release `name` that is the empty string). **Still written from documentation only:** no contributors (204), a rate limit during the read, a token. **Also seen** (capture kept): `torvalds/linux` gave a plain 403 for the list and a 404 for the release, and called its licence "Other" with no identifier, which exposed a wording defect now fixed. The page was used once against real data by a scripted browser (no console errors), which found the "Confirmed" labelling defect, also fixed |
| The PyPI and npm readers, against the real registries | `python scripts/ingest_package.py --pypi flask --kind framework` and `--npm express --kind framework`, run by the project owner on 2026-10-30 | **Reported, not witnessed by me:** both exit 0; Flask 6 unconditional dependencies recorded and 3 conditional ones left out, Express 28; every entity has evidence (7 and 29 evidence rows). Nothing is stored. See [REGISTRIES.md](REGISTRIES.md) |
| The 304 probe, against the real `api.github.com` | `python scripts/github_304_probe.py`, run by the project owner on 2026-10-28 (no token) | **Reported, not witnessed by me:** a plain read cost 1 request and each of two conditional reads answered 304 and cost 1 each. My earlier claim that a 304 is free was wrong; see "Does a 304 cost a request?" in [GITHUB.md](GITHUB.md). With a token: not run |
| The same script, run against the real `api.github.com` | `python scripts/live_github_check.py --capture captures`, run by the project owner on 2026-10-26 (Python 3.12.14, no token) and relayed through Antigravity | **Reported, not witnessed by me:** 12 PASS and exit 0; `pallets/flask` read (75,088 stars), a non-existent repository answered 404, four responses captured. The page, used by hand, showed the same figures and links with no console error. One repository, no token: see "What the real run covered" in [GITHUB.md](GITHUB.md) |
| Generated files are current | `scripts/generate_*.py --check` (OpenAPI, seed SQL, API docs, design tokens, TS types) | all up to date |
| Python compiles | `python3 -m compileall -q ...` | OK |
| Python syntax is valid for 3.12 | `ast.parse(..., feature_version=(3, 12))` over every file | OK (syntax only; the tests ran on 3.13) |
| Line length (120, the ruff limit) | `tests/test_repo_hygiene.py` | no Python line over the limit |
| Secrets in the tree | `tests/test_repo_hygiene.py` (credential-shaped strings, stray `.env`) | none; `.env.example` ships empty keys |
| Compose resolves | `docker compose config` for the default, `seed` and `app` profiles | OK; missing passwords fail loudly |
| Dockerfile and `.dockerignore` rules | `tests/test_docker_config.py` | 39 pass |
| CI workflow structure (jobs, read-only token, no secrets, script exists and is executable, Python 3.12 in the matrix) | `tests/test_ci_workflow.py` | 11 pass; skipped where PyYAML is absent (it is not a project dependency). **Says nothing about whether GitHub runs it** |
| Seed-dev container command | the Dockerfile's `CMD` and `HEALTHCHECK` run on the host | serves, health probe passes, writes nothing to the source tree |

Where a test could be vacuous it was checked by mutation or by a positive control: the level-of-detail
thresholds (a mutation exposed a tautological test, now pinned by a constants test), the `.dockerignore`
secret patterns (the earlier patterns provably missed `apps/api/.env`, a nested `*.pem` and others), and
positive samples for the secret scanner and the production-readiness guard. For the prototype, eight
deliberate breakages were each caught by a test, after a control run showed the same test commands pass on
unmodified code (remove the path-traversal guard; allow `unsafe-inline`; serve any file type; let labels
overlap labels; let labels sit on marks; ignore the visible bounds; let relationship phrases overlap; report
incomplete data as complete). That check was run by hand and is not a committed script. A first attempt
used a test command that could not even import, which made three of the eight look caught for the wrong
reason; the control run is what exposed it. The Chromium run also found two real bugs that unit tests had
not (a notice about a dropped address parameter was hidden the moment it appeared; clearing a selection
replaced a history entry, so Back did not return to it). Both are fixed and pinned.

The GitHub work added its own checks of the same kind, each a one-line breakage that made tests fail, run on
2026-10-25 after a control run on the unmodified code: the Host-header check not called (5 failures and 12
errors), a response shorter than its declared length accepted (2), a repository GitHub does not call public
analysed (5), a different repository than the one asked for accepted (1), the language bar given the header's
CSS class name (2), the repository page's container given the dossier's prose class (1), and an inline event
handler on the repository form (1). Run by hand, not a committed script. Real defects found on the way, each
before it could ship: a truncated response body was returned as a normal answer and would have been cached
under its ETag and replayed on every 304 (found by a test written first, fixed in the transport); in the
first Chromium look at the repository page, a CSS class name shared with the top bar collapsed the header over
the page, and another shared with the dossier's prose restyled every figure (neither was visible to any test;
both are now fixed and guarded by a test that fails if the repository section redefines a class the map
already styles).

The map work of 2026-11-09 (timeline strip, phone layout) had five one-line breakages run on a scratch copy after a
control run of the unit tests on unmodified code, each caught by at least one test: dating an entity by its latest
event instead of its earliest, placing forecasts on the axis, an opening zoom that ignores the window, the wrong
singular in the strip's undated sentence, and a clamp notice for a year that needs none. Run by hand, not a committed
script, and none of them against the Chromium run. That run found real defects the unit tests could not: a
stylesheet rule written above the base rule it was meant to override left the Repository link hidden in the phone's
Layers rail, and three forced-colours rules for the strip were silently overridden (two phone and three forced-colours
rules in all). Both are fixed, and `tests/test_prototype.py` now fails when a media-query rule is silenced by a later
plain rule for the same selector. Looking at the phone screenshots also found a comma starting a line in the year list
and map text sitting on region names, both fixed.

## Phases

| # | Phase | Status |
| --- | --- | --- |
| 1 | Repository inspection and architecture | Done. `docs/ARCHITECTURE.md` written and refreshed on 2026-10-11 to match what was built |
| 2 | Application shell and design system | Partial. Tokens, generated CSS/TS, typed client, state and error copy are verified. **No Next.js app** |
| 3 | PostgreSQL and Neo4j | Partial. 5 migrations (28 tables) linted statically and **never run on PostgreSQL**; Neo4j projection not started; compose only |
| 4 | Core entity model | Domain model and in-memory store done and tested. Persistence adapters not written |
| 5 | GitHub integration | Parsing, normalisation, pagination, rate-limit, retry, conditional requests, budget and transport, now composed into a **live repository reader** (provider, routes, a repository page) and run end to end against a local stand-in with fixture data, and **reported passing once against the real GitHub** (2026-10-26, one repository, no token; not witnessed by me). Token use, real rate limits, redirects and other kinds of repository are UNVERIFIED against GitHub. An opt-in read of top contributors and the latest release is written, tested against the stand-in, and was seen once against the real GitHub (2026-11-02, one repository; its 204 answer is UNVERIFIED). An opt-in year of weekly commit activity is written from two real answers (2026-11-04) and tested; GitHub's reading is considered **finished for now** (see GITHUB.md). No ingestion jobs; dependency analysis not started |
| 6 | Graph engine | In-memory graph, BFS, shortest path, components, PageRank, community detection, fuzzy search verified on 60 seed entities. Not on Neo4j; not benchmarked at scale |
| 7 | Interactive global map | Partial. A **dependency-free SVG prototype** ([PROTOTYPE.md](PROTOTYPE.md)) draws the 60 seed entities, relationships and 20 clusters with level of detail, collision-free labels, search, keyboard and touch use (opening at cluster level on a phone), a year slider and a text outline; verified in Chromium only. **No WebGL renderer, no Next.js page, never seen at more than 60 entities** |
| 8 | Entity explorer | API endpoints verified on seed data. In the prototype: a details panel for an entity or cluster and an outline view, and a separate repository page for one GitHub repository. No `/explore` or `/entity/[id]` pages |
| 9 | Timeline | API verified on seed data. The prototype lists an entity's dated events in its details panel and has a **timeline strip** that shows the map as of a chosen year (an entity is dated by its earliest event; entities with no event stay, dimmed). **Seed dates only**, Chromium only; no real release dates yet, no era or event browsing, no `/timeline` page |
| 10 | Software Genome | Not started (repository reading is its first input; dependency manifests are not built). A package's declared dependencies can be read one package at a time from PyPI and npm (the dependencies page); that is not the genome |
| 11 | Digital Archaeology | Not started |
| 12 | Gemini research system | Provider protocols and a strict result parser/validator written and tested. **No Gemini adapter**, no orchestrator; research routes answer 501 |
| 13 | Future Lab | Not started (route answers 501) |
| 14 | Authentication and user features | Tables written (migration 0004); routes answer 501; nothing implemented |
| 15 | Performance and security | API controls verified (below). No load test, dependency audit, or security review has been done |
| 16 | Testing | Unit, contract, and cross-language tests as recorded above, plus one scripted Chromium run of the prototype. No database, end-to-end-in-CI, cross-browser, load, or automated visual-regression tests |
| 17 | Deployment | Dockerfiles, compose, `.dockerignore`, CI file and `docs/DEPLOYMENT.md` written. **No image built, CI never run** |

## What each part is, and how far it is verified

### Backend core (`apps/api`)

| Part | Verified by | Not verified |
| --- | --- | --- |
| Domain: 14 entity kinds, 20 relation types, 7 provenance labels, evidence rule (a `sourced` claim needs evidence) | unit tests; enums cross-checked against SQL enums and generated TS | - |
| Config: environment parsing, `Secret` wrapper, production strictness | unit tests | behaviour under a real process manager |
| In-memory graph, algorithms, search ranking, timeline | unit tests on seed data | scale, concurrency |
| HTTP application (`Application.handle`): routing, strict validation, error envelope, signed cursors, ETag/304, rate limits, security headers | unit tests; WSGI binding served over a socket; TypeScript client exercised against it | the FastAPI/ASGI binding |
| OpenAPI 3.1 document, generated from the route registry | freshness test; internal consistency tests | no external OpenAPI validator was run |
| Routes: 33 in the registry, 20 implemented, 13 planned (answer 501) | route table is generated and tested | the 13 planned routes |
| Host-header allow-list (DNS-rebinding defence for the development servers on a loopback address) | 8 unit tests, WSGI and stand-in tests, a deliberate breakage | a deployment behind a proxy, where the allowed names are a configuration question not yet designed |
| SQL: migration planner, checksum check, statement splitter, static linter | unit tests | **execution on PostgreSQL**; the advisory-lock executor is described in `db/migrations.py` but not written |
| GitHub: URL parser, normaliser, paginator, rate-limit and retry policy, conditional requests, request budget | unit tests with fixtures written from the documented response shapes | **live responses**; the fixtures were written from documentation, not captured |
| GitHub live reader: provider, `cache_status` and `fetched_at`, private and mismatched repositories refused, error mapping that never repeats upstream text, token handling, `GET /github/repositories/{owner}/{repo}`, its `/activity` and `GET /github/rate-limit` | 32 provider tests and 22 for the activity read, route and schema tests, 41 real-socket tests against the stand-in, TypeScript client tests, 103 Chromium checks | **everything about the real GitHub**: that it sends the shapes the stand-in serves, that a token works, what the real limits do in use. The in-memory cache is per process; no Redis cache |
| Package registries: the reader (names checked, one https host, no redirects, size cap, fixed error text), the in-memory copy (10 minutes, 256 packages, a stale copy only for a failure), `GET /registries/{registry}/package`, `GET /registries/{registry}/releases`, `scripts/ingest_package.py` | 161 unit tests (52 readers and command, 73 routes, caches and references, 36 release dates), 51 stand-in and capture-check tests, 190 Chromium checks on the page that uses it; 11 deliberate breakages of the reader, route and page and 18 of the release code were each caught ([REGISTRIES.md](REGISTRIES.md)) | **PyPI and npm beyond one run**: the package route was reported passing against both on 2026-11-15 (8 packages and 2 missing names, one machine, relayed; one count checked by me against npm); not seen in real data: a dependency not readable by name, a 429 or 5xx, a stale copy; **the releases route and the dates it reads (PyPI's deprecated `releases` key, npm's `time`) have never been run against either registry**; fixtures are still hand-written |
| AI: provider protocols, strict parsing of model output, provenance downgrade for uncited claims | unit tests | any real model; the Gemini SDK and model id are undecided |
| `fastapi_app.py` | parses; imports only expected packages; names it uses from VYRON exist | **never imported or run** |
| `ruff`, `mypy --strict`, `pytest` configuration in `pyproject.toml` | `ruff` 0.16.9 and `mypy` 2.3.1 run once on 2026-11-14 outside the project's toolchain (see register item 1) | **the locked toolchain (`uv sync`) and `pytest` have never run**; `ruff check` reports 51 findings and `ruff format --check` would reformat 46 of 115 files; `mypy --strict` reports 73 |

Security controls verified at the HTTP-application level: strict query validation (unknown, repeated and
control-character parameters rejected), 64 KiB body cap, uniform error envelope with no internals,
header-injection-safe request ids, HMAC-signed expiring cursors bound to the operation, per-bucket rate
limits with trusted-proxy handling, security headers on every response, secrets hidden from `repr`,
`str` and f-strings. Not done: authentication, authorisation, CSRF design for cookies, log redaction
(there is no structured logging yet), dependency audit, penetration testing, TLS termination, and
anything that depends on a deployed environment. Details are in [SECURITY.md](SECURITY.md).

### Data

- The only data is a development seed: **60 entities, 68 relationships and 41 timeline events**, every
  one labelled `seed`. Responses that contain it carry `data_notice: "Development seed data. Not
  verified production facts."` and `/api/v1/meta` reports `data_mode: "seed"`. Production mode refuses to
  start on seed data unless `ALLOW_SEED_DATA=true`.
- No real technology data is served. `scripts/ingest_package.py` can read one PyPI or npm package's declared
  dependencies into an in-memory graph ([REGISTRIES.md](REGISTRIES.md)), and the repository page reads live GitHub
  data, but neither feeds the graph the API serves and nothing is stored. The brief's "real APIs and real
  databases" requirement is **not met yet**; it needs a database adapter and ingestion that writes to it.

### Frontend foundation (`apps/web/design`, `packages/*`)

| Part | Verified by | Not verified |
| --- | --- | --- |
| Colour tokens: contrast ratios, colour-blind separation (computed), 4 data groups plus 8 shapes | `tests/test_design_contract.py` | how it looks on a screen |
| Generated `tokens.css` and `tokens.ts` | freshness check | CSS in a browser |
| Generated TypeScript API types and route table | freshness test, `tsc --strict`, enum parity with Python | - |
| Typed API client, error model, page walker, base-URL validation | 23 unit tests, 15 integration tests against the real API, compile-time assertions | behaviour in a browser or under Next.js |
| Graph geometry for the map: atlas indexes, seeded force layout, camera, glyph shapes, cluster hulls, level of detail, label and relationship-phrase placement, keyboard navigation, accessible outline, URL state, timeline model (dating entities by their earliest event, year buckets, axis ticks) | 144 unit tests in `packages/graph`; used by the prototype and seen working there | at more than 60 entities; in a WebGL renderer |
| Error and empty-state copy | 13 unit tests | in context on screens |
| Browser prototype: SVG map, rail, details panel, search combobox, outline, address and history handling, one-origin dev server with a strict content security policy | 43 page-logic tests, 7 data tests against the real API, 52 server and page contract tests (both pages), `tsc --strict`, the map's 134 Chromium checks and screenshots | Firefox, Safari, real touch devices, screen readers, an accessibility checker, the shipped fonts, anything above 60 entities (see [PROTOTYPE.md](PROTOTYPE.md)) |
| Browser prototype, dependencies page: reads one PyPI or npm package at a time, states its provenance and age, draws the graph as it grows, with a text outline | 38 model tests, 47 graph tests, 30 release-history wording tests, 26 release-strip tests, the page contract tests, 190 Chromium checks and 13 screenshots against stand-in fixture data | **the page showing a real PyPI or npm answer** (the package route behind it was reported passing against both on 2026-11-15; the release route has not been run against either); forced-colours mode; a screen reader; a graph of hundreds of packages (the page caps it at 200) |
| Browser prototype, repository page: reads one public repository, states its provenance and age, explains every failure | 67 model tests, the page contract tests, 138 Chromium checks and 14 screenshots against stand-in fixture data, plus 35 tests of VYRON's handling of real captured GitHub responses (`test_github_real_captures.py`) | the real GitHub (the commit-activity chart has only been seen drawn from made-up counts); the same browser and device gaps as the map; a screen reader |
| Next.js app, routing, server rendering, React components, WebGL renderer, other map modes, fonts, motion in the real design, command palette, accessibility beyond computed contrast and the prototype's DOM checks | - | **UNVERIFIED and not written** (specified in `docs/FRONTEND.md` and `docs/COMPONENTS.md`) |

One API change came with the prototype: cluster summaries now carry `member_ids` (at most 300, most central
first), because a territory cannot be drawn without its members. It is covered by tests and appears in the two
generated files that describe response shapes, `docs/openapi.json` and `packages/types/src/api.ts`, both
checked for freshness. (`docs/API.md` is a route table and does not list response fields.)

### Infrastructure

| Part | Verified by | Not verified |
| --- | --- | --- |
| `docker-compose.yml` (data stores, `seed` and `app` profiles, hardening settings) | `docker compose config`; assertions on the resolved model | `docker compose up` |
| `apps/api/Dockerfile` (`seed-dev` and `runtime` targets) | lint rules; the `seed-dev` commands run on the host | **any image build**; the `runtime` target needs the packages |
| `apps/web/Dockerfile` | structural lint only | **template**; it references files that do not exist |
| `.github/workflows/ci.yml` | YAML structure | **never executed**; actions are tagged by version, not pinned by commit hash |
| Empty placeholders: `services/*`, `packages/config`, `infrastructure/neo4j` | - | contain no code |

## UNVERIFIED register

Each item names the evidence that would close it.

1. Lint, format and types: run `uv sync --all-groups && uv run ruff check . && uv run ruff format --check . && uv run mypy`.
   **Partly seen (2026-11-14), not the locked toolchain.** `ruff` 0.16.9 and `mypy` 2.3.1 were found installed here and were
   run with the project's `pyproject.toml` rules (mypy without the pydantic plugin, `fastapi_app.py` excluded). `ruff check
   src tests` first reported 68 findings; the files this feature added or changed were made clean (import order, unused
   `noqa`, an explicit `check=` on a subprocess, one blind `assertRaises`), leaving **51** in older code (21 `# fmt: skip`
   comments that ruff calls invalid, 19 of them in `routes.py`; import order in 6 files; 9 `0.0.0.0` strings in tests; 7 unused
   `noqa`; and a few others). `ruff format --check` says 46 of 115 files would be reformatted; no formatting pass has been run, because
   it would touch most of the repository. `mypy --strict` reports **73 errors in 21 files: 7 in `src` (in
   `db/lint.py`, `github/`, `api/apidocs.py` and `api/wsgi.py`, none in `registries/`) and 66 in tests**, many of them fake
   transports passed where a `urllib` opener is declared. None of it has been fixed beyond the new code. Versions and
   rules may differ from what `uv` would install.
2. Test runner: `uv run pytest` (tests are `unittest` classes; pytest should collect them but has not).
3. Python 3.12 at run time: only syntax was parsed on 3.12 rules.
4. FastAPI binding: write `tests/test_api_asgi_contract.py` as described in `fastapi_app.py` and replay the WSGI samples.
5. Migrations on PostgreSQL 16: apply `0001`..`0005` to an empty database, then the seed; write the PostgreSQL `Executor`.
6. Adapters: PostgreSQL repository, Neo4j projection, Redis cache and rate limiter are not written.
7. GitHub: partly closed. The owner's reported run (2026-10-26) passed against the real GitHub for one
   repository without a token. Since then the captures arrived (2026-10-28): real responses for an active
   repository (`pallets/flask`), an archived one (`atom/atom`) and a renamed one (`joyent/node`, a real 301) are
   kept under `apps/api/tests/fixtures/github_real/` and exercised by 12 tests (19 now, with the contributors and latest-release capture), so those three cases are now
   observed. The hand-written fixtures differed from them in four small ways (weak ETags, a `last-modified`
   validator, the 404 body, the 301's `location` header being absent from the capture); none changed VYRON's
   behaviour, and the differences are listed in `github_fakes.py`. Still open: run with `GITHUB_TOKEN`; observe
   a real rate limit (403 or 429), a repository without a licence or description, a private one, and a 5xx. Until those are seen, those paths are UNVERIFIED against GitHub.
   The opt-in contributors and latest-release read (added 2026-11-01) was run against the real GitHub once
   (reported 2026-11-02: 27 checks, exit 0, `pallets/flask`); a list of ten and a release were seen and are kept
   as fixtures. Its other answers are still written from GitHub's documentation only: no contributors (204 or an
   empty list), a rate limit in the middle of the read, a token. Later the same day (reported) the check was run on
   `torvalds/linux` (exit 0, 22 checks) and its capture is kept and tested: the contributor list is refused with a
   plain 403 (`"The history or contributor list is too large ..."`, no `Retry-After`, quota left), which VYRON reads
   as `declined`; `/releases/latest` is a 404; each cost one request. The capture also showed that GitHub calls this
   repository's licence "Other" with no SPDX identifier, which VYRON had been showing as "No license declared"; it
   now keeps GitHub's name (`license_name`) and says what GitHub said. The owner's scripted browser also used the page on
   `pallets/flask` against the real GitHub with no console errors, and showed the badge "Confirmed" where it should
   have said "Live"; fixed (a result with parts read now is no longer called "Confirmed") and tested.
   A later screenshot (2026-11-03) still showed "Confirmed" for analyse, wait 70 s, Load. **Settled the same day**
   by `scripts/live_github_check.py --flow` against the real GitHub (reported; output kept in `docs/evidence/`; 21
   checks, exit 0): the repository answered 304 and the contributors and release 200, so the label is "Live"; a repeat
   answered 304 for all three, so "Confirmed" is correct there. A freshly started server showed "Live" for the original
   sequence. The earlier screenshot most likely came from a server still running the pre-fix code (not provable). Also
   seen for the first time: 304 for `/contributors` and `/releases/latest`, each costing one request without a token,
   and the licence tag "License Other (no standard identifier)" on the real `torvalds/linux` page.
   Update (reported 2026-10-27 and 2026-10-28): a clean repeat showed `Live` then `Confirmed`, so a real 304
   revalidation was seen, and `scripts/github_304_probe.py` measured that, without a token, each 304 costs one
   request (my earlier claim that a 304 is free was wrong, and is corrected in the docs and code comments).
   Still open: the same measurement with a token.
8. Gemini: install the SDK, read its interface, pick and verify a model id, write the adapter and a recorded-response test.
9. Docker: `docker compose --profile seed up --build`, then the `app` profile once the web image exists.
10. Next.js application, React components, WebGL map, accessibility audit with a real checker, browser tests
    run in CI. (The prototype's scripted Chromium run is local only and not part of `verify_stdlib.sh`.)
11. CI: push and observe a run; pin third-party actions by commit hash.
12. Fonts and their licences: recorded from memory; confirm when the font files are fetched.
13. Performance: no budget has been measured. Targets live in `docs/FRONTEND.md`.
14. Other platforms: every check I ran was on Linux with Node 22.22. A separate run on Windows with Node 24.20.0,
    reported by the project owner and not witnessed by me, saw the API suite pass (412), the root suite pass
    (148 tests, 24 skipped because Docker and PyYAML were absent there), and the Node summaries parse, so the
    Windows `SYSTEMROOT` handling and the pinned TAP reporter are reported working. Not covered by that report:
    the 24 skipped tests (Compose resolution, CI-workflow structure), `verify_stdlib.sh` (needs bash) and `tsc`.
    That report predates the prototype, so it covers none of the prototype's tests, the emit step or `serve.py`.
    A second Windows run, again reported by the project owner and not witnessed by me, covered the prototype: it
    reports the server starting, the map, search, details and outline working in a browser without console
    errors, and API 415, root 180 (25 skipped) and TypeScript 176 passing. It found one real defect. I confirmed by
    scanning the code that 12 `read_text` and `write_text` calls in `tests/test_prototype.py` named no encoding,
    so on Windows they use the locale encoding (often cp1252) and can fail on non-ASCII text; I could not
    reproduce the failure itself on Linux, where the default is UTF-8. They now
    say `encoding="utf-8"`, and a repository test fails if any Python file reads or writes text without one.
    I have not run anything on Windows myself. The one skip beyond the earlier 24 is, from reading the code, the
    symlink test (it skips where symlinks cannot be created), so the guard against a symlink pointing out of the
    build directory is untested on Windows; that is my inference, not something the report says.
15. Browsers and devices for the prototype: I used only Chromium 141 on Linux, with touch emulated; the project
    owner reports a Windows run (item 14), which I did not see and whose browser was not named. Firefox, Safari,
    a real phone or tablet, high-contrast mode in practice, and the `:has()` error layout elsewhere are untried.
16. Screen readers and accessibility checkers: roles, labels, live regions and focus order were checked in
    the DOM only. No screen reader, no axe-style audit; the contrast of the dimmed state was not measured.
17. Scale of the SVG renderer: nothing above 60 entities has been drawn, laid out or timed. Closing this needs
    a larger dataset (the real ingestion, or a generated one clearly labelled as such) and a measurement.
18. Fonts: Newsreader and Hanken Grotesk are named but not shipped, so screenshots show fallback fonts. Text
    widths, and therefore label placement, can differ with the real fonts; re-run the Chromium script once
    they are bundled.
19. The prototype's dev server (`serve.py`) and its headers are for local use. Production headers, TLS and
    caching have not been designed or tested.
20. The GitHub quota on a shared server: anyone who can reach a server that holds `GITHUB_TOKEN` can spend its
    quota. The development server refuses a token on a non-loopback address unless told otherwise, and the
    settings-driven composition has no GitHub off-switch yet. The per-client limit (30 a minute) and the cache
    are in-process, so several instances would each have their own.
21. The map page's top bar: since 2026-11-09 the Repository and Dependencies links sit in the Layers rail on a phone,
    and a Chromium check (at 390 px) says the bar takes two rows. 375 px is not separately measured for the bar.
22. The package route: 2026-11-15: the project owner ran `scripts/capture_registry.py` on their Windows machine (exit code 0, no token) and relayed the console output; I did not see the run. All 10 checks passed, each against the API's response schema: flask 3.1.3 (6 dependencies, 3 left out as conditional), requests 2.34.2 (4, 2 conditional), numpy 2.5.4 (0), django 6.1.3 (2, 3 conditional), express 5.3.0 (28, 16 development left out), react 19.3.1 (0), @types/node 26.6.6 (1), left-pad 1.3.0 (0, 3 development), and a name that does not exist on each registry (404 from the registry and from VYRON). I checked one figure independently: npm's own `express/latest` says 5.3.0 with 28 dependencies and 16 devDependencies, equal to the relayed numbers (PyPI could not be fetched from here; the flask numbers equal the command's 2026-10-30 report). I have not compared the other counts with the packages' pages. What it does not show: one run, one day, one machine. The 10 raw answers (a 3.8 MB zip) did not reach me, so the fixtures are still hand-written, not captured. None of the 8 packages had a dependency that is not readable by name, so that path (a URL, path or alias requirement), a registry 429 or 5xx, a stale copy and a package with a condition VYRON cannot read have still not been seen in real data. **The page has not been seen showing a real answer.** The fixtures' shapes (written from documentation and the reported flask
    and express shapes) stay unconfirmed until a raw capture is used to build one.
23. The dependencies page: forced-colours mode (rules written, never run), screen readers, and any graph near the 200
    package cap were not tried. Whether the layered layout stays readable for hundreds of packages is unknown.
24. The release history (2026-11-19): the releases route and the page section were written and run only against
    `scripts/standin_registry.py`, whose dates are made up and whose shapes I wrote from the registries' documentation.
    **Nobody has seen the real shape of PyPI's `releases` object or npm's `time` object through this code.** PyPI documents
    the `releases` key as deprecated, so the route may stop working on PyPI one day (it then answers 502 with fixed words and
    the page says the dependencies are unaffected). `scripts/capture_registry.py` now checks each package's release dates and
    fails when the registry's own document has none, but **it has not been run** since that check was added. Also not tried:
    forced-colours mode, a screen reader, Back and Forward on this section, a history of thousands of versions (272 was the
    largest), and anyone using the 9 px marks with a pointer (the arrow keys and two buttons of at least 32 px do the same job).
    Dates are when a file reached PyPI or npm's recorded publish time, which is not a statement about when anything was
    released or whether a package is maintained.

## Next steps (once installs work)

1. `cd apps/api && uv sync --all-groups && uv lock`; fix whatever `ruff`, `mypy` and `pytest` report.
2. Bind the FastAPI app and replay the contract samples through its test client.
3. Apply the migrations on PostgreSQL; write the PostgreSQL repository and run the existing in-memory tests against it.
4. Scaffold `apps/web` with Next.js; replace relative imports between packages with workspace imports; build the shell, `/explore`, `/entity/[id]`, then the map.
5. GitHub reading is finished for now (2026-11-05): the repository, contributors, latest release and a year of commit
   activity. The answers still unseen (no contributors, a rate limit, a token, a 204 for commit activity) wait for
   whenever they happen to be observed; nobody is to run more rounds for them. Next: the package dependency graph is
   built (2026-11-14; the route reported passing against the real registries on 2026-11-15, the page unseen with real data) and so is its
   release history (2026-11-19; the dates never read from the real registries: run `capture_registry.py` once to see them); then a Technology DNA card, compare,
   share and export, and a deterministic evidence pack for the AI layer, then GitHub dependency manifests and ingestion,
   then the Gemini adapter behind the existing provider protocols.

Without package access, the prototype is the furthest the map can go: it can be run and reviewed
([PROTOTYPE.md](PROTOTYPE.md)), but the Next.js app, the WebGL renderer, real data and every service-backed
feature stay UNVERIFIED until the installs work. The prototype's geometry package and its page logic are
written so they can move into the Next.js app unchanged; that move has not been tried.

## Documents

[ARCHITECTURE](ARCHITECTURE.md), [API](API.md) (route table generated), [openapi.json](openapi.json)
(generated), [DATABASE](DATABASE.md), [GITHUB](GITHUB.md), [AI](AI.md), [SECURITY](SECURITY.md),
[FRONTEND](FRONTEND.md), [COMPONENTS](COMPONENTS.md), [PROTOTYPE](PROTOTYPE.md) (the browser prototype and
its screenshots in `prototype/`), [DEPLOYMENT](DEPLOYMENT.md),
[CONTRIBUTING](CONTRIBUTING.md), and this file. The documents describe the design and the current
state separately; where they disagree with the code or with this file, this file and the code win.

## Re-running everything

```bash
scripts/verify_stdlib.sh            # reports skips when a tool is missing
scripts/verify_stdlib.sh --strict   # a missing tool is a failure (used by CI)

python3 scripts/build_prototype.py                 # compile the browser prototype
python3 apps/web/prototype/serve.py                # open http://127.0.0.1:8080/ (the repository page reads the real GitHub)
python3 apps/web/prototype/serve.py --no-github    # the same with GitHub off
python3 scripts/standin_github.py --mode prototype # the prototype wired to a GitHub stand-in (FIXTURE DATA)
python3 scripts/live_github_check.py               # the check against the real GitHub; run where GitHub is reachable
python3 scripts/verify_prototype_browser.py        # needs playwright + Chromium; refreshes docs/prototype/
```
