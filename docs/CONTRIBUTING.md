# Contributing

VYRON is early. Most of the product is not written (see [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)),
so the most useful contributions are the ones that move something from "written" to "verified", or from
"not written" to "written and tested". This guide says how to check your work and what the project will not
accept. A licence has not been chosen yet; do not assume one.

## Ground rules

1. **No invented facts.** Every entity, relationship, timeline event and AI claim carries a provenance
   label (`sourced`, `estimated`, `inferred`, `predicted`, `user_generated`, `ai_generated`, `seed`). Only
   `sourced` needs evidence, and evidence means a source a provider actually returned. Never write a
   citation, URL, statistic or benchmark that was not retrieved or measured.
2. **Seed data is labelled as seed.** Fixtures and demo data use provenance `seed` and the standard notice.
   They are never presented as real.
3. **AI is never the source of truth.** The graph, data, timeline and deterministic analysis must keep
   working with no AI configured. Model output is untrusted input and goes through the validator.
4. **Say what you did not run.** If code needs a package, a service or a browser you could not use, mark it
   `UNVERIFIED` where it lives and in `docs/IMPLEMENTATION_STATUS.md`. Do not describe the project as
   production ready. Never write a test that cannot fail; check a new test by breaking the code once.
5. **Secrets stay out of the repository.** No real keys, tokens or passwords, in code, fixtures, docs or
   history. `.env` is ignored; use `.env.example` for names and empty values. A repository test scans for
   credential-shaped strings.
6. **Do not work around the network.** If a registry or API is blocked, report it. Do not disable TLS
   verification, unset proxy settings, or route around a refusal.

## Running the checks

Everything that needs no installed packages runs with one command:

```bash
scripts/verify_stdlib.sh            # skips a step when its tool (node, tsc, docker) is missing
scripts/verify_stdlib.sh --strict   # treats a missing tool as a failure; CI uses this
```

It needs Python 3.12 or later, Node 22.18 or later (TypeScript tests run without a flag), `tsc` for the
strict type check, and the Docker CLI (no daemon) for `docker compose config`. The individual commands:

```bash
(cd apps/api && PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests)
python3 -W error::ResourceWarning -m unittest discover -s tests     # from the repository root
node --test packages/*/src/*.test.ts
tsc -p tsconfig.check.json
```

The full toolchain, once dependencies can be installed:

```bash
cd apps/api && uv sync --all-groups
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Those four have never been run on this code; expect the first `ruff format --check` to ask for a
formatting pass.

## Generated files

Several files are generated and checked for freshness by tests. Edit the source, regenerate, commit both.

| Generated file | Source | Command |
| --- | --- | --- |
| `docs/openapi.json` | route registry and schemas in `apps/api/src/vyron_api/api` | `python3 scripts/generate_openapi.py` |
| route table in `docs/API.md` | the same registry | `python3 scripts/generate_api_docs.py` |
| `infrastructure/database/seed/dev_seed.sql` | `apps/api/src/vyron_api/seed/dev_seed.py` | `python3 scripts/generate_seed_sql.py` |
| `packages/types/src/api.ts` | `docs/openapi.json` | `python3 scripts/generate_ts_types.py` |
| `apps/web/src/styles/tokens.css`, `packages/ui/src/tokens.ts` | `apps/web/design/tokens.json` | `python3 scripts/generate_design_tokens.py` |

Each script takes `--check` and exits non-zero when the committed file is stale. They add the source
directories to the path themselves, so they run from anywhere with no `PYTHONPATH`.

## Common changes

**A new API route.** Add it to the route registry with its schemas and status (`planned` answers `501`),
regenerate OpenAPI, the API docs and the TypeScript types, implement the handler, and add tests for success,
validation errors and the error envelope. The route table and the types are derived, so tests fail when
they drift.

**A new entity kind or relation type.** The Python enum, the SQL enum (a new migration, never an edit to an
applied one), the generated TypeScript values and the design tokens (colour group and shape) must agree.
Tests compare them and fail on any difference.

**A schema change.** Add `NNNN_name.sql` with the next number. Applied migrations are immutable (the
checksum check refuses a changed one). The SQL linter must pass; it is not a substitute for running the
migration on PostgreSQL.

**A colour or type change.** Edit `apps/web/design/tokens.json` only. The contrast and colour-blind
separation checks run in `tests/test_design_contract.py`; do not weaken a threshold to make a colour pass.

## Code conventions

- **Python.** 3.12 syntax or older (a test parses every file with the 3.12 grammar), lines up to 120
  characters (enforced by a test and by ruff), type annotations on everything, no tabs or trailing
  whitespace. The core (`domain`, `graph`, `github`, `api`, `db`, `config`) imports only the standard
  library, and that is deliberate: it keeps it testable here. Third-party imports belong in the adapters
  and in `fastapi_app.py`.
- **TypeScript.** Erasable syntax only (no `enum`, `namespace` or parameter properties), because Node strips
  types rather than compiling them. Relative imports carry the `.ts` extension. Packages import only
  relative files until workspaces exist. Non-test sources may not import from `node:`.
- **Errors.** Return the standard error envelope. Messages never contain internals (SQL, stack traces,
  upstream bodies, tokens).
- **Untrusted input.** Parse, don't repair: reject what you do not recognise. Cap sizes. Never fetch a URL a
  user supplied.
- **Tests.** Prefer testing behaviour through the public function over testing internals. Fixtures for
  external services are written from the documentation and say so; a fixture captured from a real
  response says that instead.
- **Copy.** Interface text is plain, in sentence case, names things by what the person sees, and errors say
  what happened and what to do next. See `packages/ui/src/states.ts`.

## Commits and pull requests

Keep a change focused and describe what was verified and what was not. If a change touches a generated
file, include the regenerated output. A pull request that adds a dependency should say why the standard
library or an existing dependency is not enough.
