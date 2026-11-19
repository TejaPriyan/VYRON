# Package registries: PyPI and npm

Last updated: 2026-11-19. What `apps/api/src/vyron_api/registries` does, what it records, and how far it is
verified. Standard library only. It is not production ready.

There are two ways VYRON reads a package, and they share the same reader:

1. **The API routes** (2026-11): `GET /api/v1/registries/{registry}/package?name=…` reads **one package per
   request**, keeps the answer in memory for ten minutes, and returns it with where and when it was read. The
   [dependencies page](PROTOTYPE.md#the-dependencies-page) calls it, one request each time a package is opened.
   `GET /api/v1/registries/{registry}/releases?name=…` (2026-11-19) returns the same package's **release dates** from
   the same registry document, see [The release history route](#the-release-history-route).
2. **The command** (earlier, 2026-10): `scripts/ingest_package.py` builds one package as entities, evidence and
   relationships **in memory** for that one command. Nothing from either way is stored in a database; the
   PostgreSQL adapter does not exist.

## The API route

```bash
python3 apps/web/prototype/serve.py     # the prototype server also serves the API; add --no-registries to switch this route off
cd apps/api && PYTHONPATH=src python3 -m vyron_api.devserver   # the API alone (same flag)
curl 'http://127.0.0.1:8000/api/v1/registries/pypi/package?name=flask'
curl 'http://127.0.0.1:8000/api/v1/registries/npm/package?name=%40types%2Fnode'
```

The package name is a **query parameter**, not part of the path, because a scoped npm name contains a slash.

A `PackageReport` carries: `registry`, `name` (PyPI's canonical form; npm's exact name), `version` (the version
described), `summary`, `license`, `homepage`, `registry_url` (the registry's page for the package, or `null`, see the
stand-in below), `dependencies` (each `name`, `requirement` as written when it is plain version syntax, and
`readable`), `left_out` (what the metadata declares but VYRON does not list, as `kind` and `count`), `fetched_at`,
`cache_status` (`fresh` or `stale`), `source_url` (the exact address read) and `provenance` (always `sourced`: the
package's own registry metadata says so, and nothing more is claimed).

`readable` is false when a requirement points outside the registry (a URL, a path or an npm alias), because the
name then does not identify a package there and asking for it would read the wrong thing or nothing. The page shows
such a dependency but does not offer to open it.

| Situation | Answer |
| --- | --- |
| Read just now, or a copy under ten minutes old | `200`, `cache_status: "fresh"`, `fetched_at` is when the registry actually answered |
| The registry failed (5xx, 429, a garbled body, unreachable) and an older copy is held | `200`, `cache_status: "stale"`, with that copy's own `fetched_at`; the page says the registry could not be reached |
| The registry failed and there is no copy | `502 upstream_error` (fixed text) or `503 upstream_rate_limited` with `Retry-After: 60` |
| The registry has no such package | `404 not_found`. **Never served from an old copy**: a package that is gone is not shown as present |
| The name is not one a registry could issue, or `registry` is not `pypi` or `npm` | `422 validation_error`, with no request made |
| Started with `--no-registries` | `503 service_unavailable`, "switched off" |

The route is rate limited per client (bucket `registry`, 30 requests a minute) and sends `Cache-Control` for 60
seconds. The copy kept in memory holds at most 256 packages (the least recently used is dropped first) and is lost when the process stops.

## The release history route

```bash
curl 'http://127.0.0.1:8000/api/v1/registries/pypi/releases?name=flask'
curl 'http://127.0.0.1:8000/api/v1/registries/npm/releases?name=%40types%2Fnode'
```

A `PackageReleases` carries: `registry`, `name`, `registry_url`, `releases` (each `version`, `released_at`,
`pre_release` and `status`, **oldest first**, at most the newest 2000), `omitted_older` (how many earlier releases are
not in the list), `left_out` (at most four entries, each a `kind` and a `count`), `date_meaning`, `fetched_at`,
`cache_status`, `source_url` and `provenance` (always `sourced`: the registry records these dates, and nothing more is
claimed; it does not say a package is maintained, safe or worth using).

**What a date is differs by registry, and the answer says which** (`date_meaning`):

| Registry | Date | Where it comes from |
| --- | --- | --- |
| PyPI | `first_file_upload`: the earliest upload time among the files of that version | the `releases` object of PyPI's JSON document (`upload_time_iso_8601`, else `upload_time`, which has no zone and is read as UTC). **PyPI documents that key as deprecated**: it may go away |
| npm | `publish_time`: the registry's own `time` entry for the version | `time[version]`, for versions still listed under `versions` |

Neither is the day an author tagged or announced a release, and the page says so.

What `status` and `pre_release` are: `yanked` (PyPI) only when **every** file of the version is marked yanked;
`deprecated` (npm) when the version carries a deprecation notice; otherwise `normal`. `pre_release` is read from the
**version string only** (a `dev`, `a`, `b`, `c`, `rc`, `alpha`, `beta`, `pre` or `preview` part on PyPI, a `-suffix` after the patch number on npm); it is VYRON's reading of
the number, not something either registry says.

What is left out, counted and never silently: `undated` (the registry gave the version no usable date), `removed`
(npm keeps a `time` entry for a version that is no longer published; it is not listed), `implausible_date` (before 1991
or more than a day in the future, which cannot be right) and `unusable` (not a version string a registry would issue).
A package whose document carries no dates at all is not shown as an empty history: the route answers
`502 upstream_error` with the fixed text "PyPI sent no release dates for this package" (or "npm"). More than 50,000
versions in one document is refused, not sampled.

It is the same registry request as the dependencies route: one answer fills both in-memory copies (the 256-package
copy of the dependency report and a second copy of **64** release histories, which are the larger values), each fresh for
ten minutes. A history can be opened right after the package was read without asking the registry again, and the other
way round. The same rules hold: an older history is served only when the registry fails, labelled `stale`, and **never
for a package that does not exist** (`404`). Same rate bucket (`registry`, 30 a minute) and `Cache-Control` of 60 seconds.

## What is recorded, and what is deliberately not

| Registry | Listed as dependencies | Left out (counted, with a reason) |
| --- | --- | --- |
| PyPI | Requirements in `info.requires_dist` that carry **no condition** | `conditional`: requirements with any marker (an `extra`, a Python version, a platform). `unusable`: an entry whose name no registry could have issued |
| npm | The runtime `dependencies` of the version tagged `latest` | `development`, `peer` and `optional` dependencies, each counted; `unusable` as above. `latest` is the maintainers' tag, not necessarily the highest version |

Why PyPI markers are not evaluated: doing it correctly needs the `packaging` library, which this project does not
install (registries are unreachable here), and writing a marker evaluator by hand is how wrong answers get
recorded as facts. The first version of this code recorded Flask's `importlib-metadata` (needed only on Python
older than 3.10) as an unconditional dependency; that is the case this rule exists for.

A dependency is a name. The route reads nothing about it until it is opened (the page opens it with another
request), and nothing here says a package is maintained, safe or important. In the command's output its provenance is
`sourced` in the narrow sense that the depending package's own metadata names it, and each new entity carries
evidence saying so.

## Rules the code keeps (each has a test that fails without it)

- **Names are checked before a URL exists.** A PyPI name must match PyPI's shape and is put in its canonical
  form (PEP 503) so the request needs no redirect; an npm name must match npm's shape, scoped or not. A name such
  as `../simple/flask`, `flask?x=`, `a b` or `%2e%2e` is refused with no request.
- **One host, HTTPS, no redirects.** The reader refuses any other scheme or host, sends no credentials and no
  contact address, does not follow a redirect (it reports it), and caps the body at 16 MiB (more is an error,
  not a truncated parse).
- **Errors are fixed text.** The registry's own words, a URL and an exception's message never reach an error
  message, the API's response or the command's output.
- **Answers of the wrong shape are refused**, not guessed at: a non-object document, requirements that are not a
  list of strings, more than 500 of them, an npm document with no `latest` version.
- **A stale copy is labelled, and only for a failure.** A missing package is never answered from the copy.
- **A local stand-in is possible only by an explicit argument, never from the environment.** The one place a
  registry address can differ from the real one is the `allow_local_test_server` parameter, and then only
  `http` on `127.0.0.1` or `localhost`; any other address makes `build_live_package_provider` raise.
- **Every `sourced` entity has evidence of its own, and every relationship points at stored evidence** (the command).
- **An entity already in the graph is reused, never replaced or upgraded** (the command): ingesting Flask into the
  seed graph attaches the evidence to the seed's `flask` entity, which stays labelled `seed`.
- A registry JSON API is recorded as source type `other`, not "official documentation".
- **A date is never invented, repaired or guessed.** A version with no usable date is counted as left out; a date before
  1991 or in the future is counted, not drawn; a missing `releases` or `time` object is an error with fixed text, not an
  empty list; the registry's order is not trusted, the list is sorted by date and then version.

## The local stand-in and the capture script

The build environment cannot reach PyPI or npm, so everything above was tested against `scripts/standin_registry.py`,
a loopback server that answers in the shape of a registry. **Everything it serves is fixture data.** Every package is
named `fixture-*` (or sits in `@fixture-scope`), its description says it is made up, and `registry_url` is `null`,
which is how the dependencies page knows to say "this answer came from a local stand-in registry" on screen. The
scenarios include a package with conditional requirements, a direct-URL requirement, a cycle, a package with 40
dependencies, and packages that answer 429, 500 and a body that is not JSON.

```bash
python3 scripts/standin_registry.py --mode prototype --port 0   # the prototype page wired to it
python3 scripts/standin_registry.py --mode api --port 0         # the API wired to it
```

`scripts/capture_registry.py` is the check against the real registries, to be run on a machine that can reach them:

```bash
python3 scripts/capture_registry.py --capture captures    # 8 default packages + 2 that do not exist; ~20 requests
python3 scripts/capture_registry.py --package pypi:requests --package npm:@types/node
```

For each package it asks the registry directly (saving the raw answer with `--capture`), asks VYRON's own application
for the same package, checks the answer against the API's response schema, and prints the version, the number of
dependencies and what was left out so a person can compare with the package's page. It needs no token. Exit 0 means
every package passed, 1 a check failed, 2 a registry could not be reached. **A package VYRON cannot read is the most
useful result**: the saved raw file is the thing to build the next fixture from.

## Verification

| Claim | Evidence |
| --- | --- |
| Parsing, naming, refusal, evidence and integrity rules | `test_pypi_ingest.py` (18), `test_npm_ingest.py` (12), `test_registry_http.py` (14, including the real redirect refusal against a local server), `test_registry_integrity.py` (8, which copy an ingested graph into SQLite and run the integrity views; two were mutation-checked) |
| The routes, the reader's caches and what a dependency reference is | `test_package_route.py` (21), `test_package_provider.py` (30), `test_package_references.py` (22): the response schemas, every error mapping, fresh and stale copies, a missing package not served from a copy, the stand-in refused without the argument, the rate bucket, one registry request filling both caches, the 64-history limit |
| Release dates | `test_release_dates.py` (36): earliest file, naive timestamps as UTC, a version all of whose files are yanked, deprecation, `removed` versions, the 1991 and future limits, the 2000 cap and `omitted_older`, pre-release by number, a document with no dates |
| The stand-in and the capture script | `tests/test_standin_registry.py` (38), including running `capture_registry.py` against the stand-in as a subprocess; `tests/test_capture_registry_checks.py` (13), which feeds the script's release checks answers that have been tampered with and expects each to fail |
| Deliberate bugs are caught (2026-11-14, by hand, each after a control run on unmodified code) | 6 of 6 in the API reader and route made a test fail: a stale copy served for a missing package; the fresh window ignored; a stale answer labelled fresh; any host accepted; the stand-in allowed without its argument; the route moved to a different rate bucket. Not a committed script |
| Deliberate bugs in the release code are caught (2026-11-19, by hand, on a scratch copy, each after a control run) | 18 of 18 made a test fail: PyPI's latest file taken instead of the earliest; yanked when any (not every) file is yanked; no fallback to `upload_time`; implausible dates kept (PyPI and npm); npm's deprecation ignored; `removed` not counted; a zone-less timestamp not read as UTC; a missing-dates answer turned into an empty list; the history cache never evicting; a stale history labelled fresh; and, in the timeline logic and wording, no row packing, overlaps not counted, End going to Home, a yanked version drawn as a circle, a pre-release not noted, the date meaning claiming the tag day, omitted and crowded releases left unsaid. The first run of the 11 provider/route cases showed `SURVIVED` for all of them only because I had pointed it at test modules that live elsewhere (a bad control); the control was fixed and they were re-run. Not a committed script |
| It reads the real PyPI and npm | **Reported for the command, not witnessed by me.** The project owner ran `ingest_package.py` on their own machine and relayed the output. With the first version of this code (2026-10-28): Flask v3.1.3 with seven dependencies, Express v5.3.0 with 28. With the 2026-10-30 version (exit code 0 each): `--pypi flask --kind framework` recorded **6** dependencies, left **3** out as conditional and built 7 entities, 6 relationships and 7 evidence rows; `--npm express --kind framework` recorded 28 runtime dependencies and built 29 entities, 28 relationships and 29 evidence rows. One run each, one day, one machine |
| **The API route against PyPI and npm** | **Reported passing, not witnessed by me.** 2026-11-15: the project owner ran `scripts/capture_registry.py` on their Windows machine (exit code 0, no token) and relayed the console output; I did not see the run. All 10 checks passed, each against the API's response schema: flask 3.1.3 (6 dependencies, 3 left out as conditional), requests 2.34.2 (4, 2 conditional), numpy 2.5.4 (0), django 6.1.3 (2, 3 conditional), express 5.3.0 (28, 16 development left out), react 19.3.1 (0), @types/node 26.6.6 (1), left-pad 1.3.0 (0, 3 development), and a name that does not exist on each registry (404 from the registry and from VYRON). I checked one figure independently: npm's own `express/latest` says 5.3.0 with 28 dependencies and 16 devDependencies, equal to the relayed numbers (PyPI could not be fetched from here; the flask numbers equal the command's 2026-10-30 report). I have not compared the other counts with the packages' pages. What it does not show: one run, one day, one machine. The 10 raw answers (a 3.8 MB zip) did not reach me, so the fixtures are still hand-written, not captured. None of the 8 packages had a dependency that is not readable by name, so that path (a URL, path or alias requirement), a registry 429 or 5xx, a stale copy and a package with a condition VYRON cannot read have still not been seen in real data. **The page has not been seen showing a real answer.** |
| **The release dates against PyPI and npm** | **Not seen at all.** The 2026-11-15 run above was made before this route existed. `capture_registry.py` now also checks the release history of each package it reads and prints, for example, `releases: N listed (YYYY-MM-DD to YYYY-MM-DD), K pre-release by number, F yanked or deprecated, left out: …; dates are first_file_upload`; it fails when the registry's own document has no `releases` (PyPI) or `time` (npm) object. Until someone runs it, **every claim about the shape of the dates is from the registries' documentation and from the answers I wrote by hand** |
| Fixtures | Hand-written from the shape of a registry's answer and the shapes reported for flask and express (`apps/api/tests/registry_fakes.py`), not captured. There are no captured PyPI or npm responses yet |

From the build environment the command answers `could not reach PyPI` (the sandbox blocks the host), which is
the expected failure and was seen.

## Limits

- Nothing is stored. The route's copy is in memory, per process, for ten minutes; a second server process has its own.
  Storing needs the PostgreSQL adapter, which does not exist.
- One package per request. The route does not follow dependencies of dependencies; the page does that one package at a
  time, when asked, and says that an unopened package has no lines out of it yet because it has not been read.
- The dependencies read is the version PyPI calls current (`info.version`) or npm tags `latest`. The package route has no
  version parameter, so "depends on" has no "since when": the release history lists the versions and their dates but
  **does not say what any older version depended on**.
- The release dates rest on a PyPI key that PyPI documents as deprecated (`releases`). If PyPI drops it, the route answers
  `502` with the fixed "sent no release dates" text and the page says so; the dependencies are not affected. Moving to
  PyPI's other interfaces would need a request per version, which is not built.
- A PyPI date is when a file reached PyPI and an npm date is the registry's `time` entry. Neither is a release
  announcement, a tag, or a statement about maintenance. A package that was re-uploaded, or published in a burst, shows
  those dates.
- Versions beyond the newest 2000 are counted (`omitted_older`) and not listed; versions without a usable date are
  counted (`left_out`) and not drawn.
- Private registries, authentication, retries and any limit beyond a registry's own 429 are not built.
- `pypi.org` and `registry.npmjs.org` answer to a package name; a typosquat or a deleted-and-reissued name looks
  the same as the real one. Nothing here checks who maintains a package.
