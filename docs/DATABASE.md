# Database

Status: **the schema is written and statically linted; it has never been executed.** No PostgreSQL server
was available and no database driver could be installed, so every statement below is UNVERIFIED against
a real server. The PostgreSQL, Neo4j and Redis adapters are not written. See
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## Who owns what

| Data | Authority | Notes |
| --- | --- | --- |
| Entities, attributes, relationships, sources, evidence | PostgreSQL | relationships carry provenance and evidence |
| Users, sessions, bookmarks, saved maps, search history, notifications | PostgreSQL | |
| Jobs, AI research runs, API usage | PostgreSQL | the job row is the durable record; any queue is transient |
| Graph traversal structure | Neo4j (derived) | id, kind, slug, name and typed edges only; rebuildable from PostgreSQL |
| Cache, rate-limit counters, queue | Redis | everything in Redis may be lost |

Neo4j is written only by a projector job, never by request code. The Neo4j projection is **not started**
(`infrastructure/neo4j` is empty); the in-memory graph in `apps/api` stands in for it today.

## Migrations

Plain SQL files in `infrastructure/database/migrations`, named `NNNN_name.sql`, forward-only.

| File | Contents |
| --- | --- |
| `0001_foundation.sql` | `pg_trgm`, 11 enumerated types, the `set_updated_at()` trigger function |
| `0002_entities.sql` | `entities`, `entity_aliases`, typed detail tables, `timeline_events` |
| `0003_evidence_relationships.sql` | `sources`, `evidence`, `relationships`, `relationship_evidence`, two integrity views |
| `0004_users.sql` | `users`, `sessions`, `bookmarks`, `saved_maps`, `search_history`, `notifications` |
| `0005_jobs_research.sql` | `jobs`, `ai_research_runs` and their entity, evidence and claim tables, `api_usage` |

28 tables in total. Requires PostgreSQL 13 or later (built-in `gen_random_uuid()`).

The migration tool (`apps/api/src/vyron_api/db/migrations.py`) is a pure planner plus an `Executor`
protocol:

- Versions must be contiguous from `0001`; a gap or a bad file name is an error.
- Each applied migration is recorded with a SHA-256 checksum (newlines normalised, so CRLF checkouts do
  not look modified). A changed checksum, a missing file, or a pending migration older than the newest
  applied one is refused. Fix a mistake with a new migration, never by editing an applied one.
- `migrate(executor, migrations, dry_run=...)` runs under `executor.lock()` and applies in order.
- **The PostgreSQL executor is not written.** Its contract is in the module docstring: a session-level
  advisory lock, each migration plus its history row in one transaction, and the history table created
  from `HISTORY_TABLE_DDL`.

## Design

- **One base table plus typed detail tables.** `entities` holds identity, kind, slug, name, provenance and
  soft deletion. Each family (`technologies`, `languages`, `organizations`, `repositories`, `ai_models`,
  `datasets`, `research_papers`) has a detail table whose `(entity_id, kind)` is a composite foreign key
  into `entities (id, kind)` with a `CHECK` on `kind`, so a `languages` row can only point at a language.
- **Slugs are unique per kind among live rows** (`WHERE deleted_at IS NULL`), so soft deletion never
  blocks reuse.
- **Fuzzy search** uses trigram indexes on `lower(name)` and `lower(alias)`.
- **Relationships** are directed and unique per `(source, target, type)`; self-loops are refused. The
  vocabulary of 20 relation types is an enum.
- **Timeline** events store a date plus a precision (`day`, `month`, `year`, `decade`).
  `is_prediction` must equal `provenance = 'predicted'`, enforced by a `CHECK`.
- **Every table has `created_at`; mutable tables have `updated_at`** kept honest by a trigger.
- **Foreign keys state `ON DELETE` explicitly** and are covered by an index.
- **Secrets are stored as hashes**: `users.password_hash` is meant to hold an Argon2id encoded hash and
  `sessions.token_hash` the SHA-256 of the opaque cookie value, so a database leak does not hand out
  sessions. No IP addresses are stored. Nothing in the repository hashes passwords yet.

## Provenance and evidence in the schema

Every entity, relationship, timeline event and AI claim has a `provenance` value:
`sourced`, `estimated`, `inferred`, `predicted`, `user_generated`, `ai_generated` or `seed`.

- A `sourced` relationship needs at least one `relationship_evidence` row, and a `sourced` entity at least
  one `evidence` row. These rules span two tables, so the application enforces them on write and two
  views report violations: `integrity_sourced_relationships_without_evidence` and
  `integrity_sourced_entities_without_evidence`. **Each must return zero rows.** Nothing runs them yet.
- `evidence` links claim, source, entity and retrieval time. `sources` only ever hold URLs a provider
  returned; the system never synthesises a citation.
- **AI citations cannot point outside what the model was shown.** A research run records the evidence it
  was given (`ai_research_run_evidence`). `ai_research_claim_evidence` has a composite foreign key into
  that set, so the database refuses a citation to evidence the run never saw. Claim provenance may not be
  `seed`.

## Seed data

`infrastructure/database/seed/dev_seed.sql` is generated from `apps/api/src/vyron_api/seed/dev_seed.py`
by `scripts/generate_seed_sql.py` (`--check` fails when stale). It holds 60 entities, 68 relationships
and 41 timeline events, all labelled `seed`, with deterministic ids derived from a fixed namespace. It
re-runs safely (`ON CONFLICT`) and must never be loaded into a production database. It is a development
fixture, not a map of the technology ecosystem.

## A local SQLite adapter (development only)

`apps/api/src/vyron_api/db/sqlite.py` and `scripts/migrate.py` (`--init`, `--seed`, `--verify`) create a SQLite file
(`vyron.db`, git-ignored), load the seed into it, and run the integrity views `integrity_sourced_entities_without_evidence`
and `integrity_sourced_relationships_without_evidence`. It exists so the "a sourced thing has evidence" rule can be run
over real rows with the standard library alone. **It is not the PostgreSQL schema.** Its DDL is a separate, hand-written
simplification (no enums, no triggers, no `timestamptz`, no partitioning, no row-level rules), so passing here says
nothing about the PostgreSQL migrations, which are still statically linted and **never run**. If the two drift, the
migrations are the source of truth. Tests: `test_sqlite_persistence.py` (3) and, for registry ingestion,
`test_registry_integrity.py` (8). The adapter has not been used with more than the seed.

## What the static checks cover

`apps/api/tests/test_sql_tooling.py` (39 tests) runs a standard-library linter over the migrations and the
seed. It checks, among other things:

- foreign keys point at existing, earlier tables and columns, with matching types, and reference a primary
  key or unique constraint;
- every foreign key is covered by a non-partial index;
- every table has a primary key; names are lower snake_case; `timestamptz` rather than `timestamp`;
  defaults are valid for their type;
- every `updated_at` has a `BEFORE UPDATE` trigger that calls `set_updated_at()`;
- SQL enums match the Python enums value for value (`EntityKind`, `RelationType`, `Provenance` and others);
- secret-like columns are named `*_hash`;
- the seed only inserts into known tables and columns, and is re-runnable.

The linter understands only the subset of DDL these migrations use, and reports anything outside that
subset instead of skipping it. **It is not a substitute for running the migrations.** It cannot find
a type error in an expression, a bad `CHECK` regular expression, a missing privilege, or a planner
problem.

## To verify when PostgreSQL is available

1. Apply `0001` to `0005` to an empty PostgreSQL 16 database; then apply the seed twice.
2. Query both integrity views; both must be empty after the seed.
3. Write the PostgreSQL `Executor` and prove the checksum, lock and rollback behaviour on it.
4. Write the PostgreSQL entity and relationship repositories and run the existing in-memory repository
   tests against them unchanged.
5. Check query plans for the search, neighbourhood and timeline queries on a realistic volume. No volume
   test exists, and no index has been justified by a measured plan.
6. Decide a retention policy for `api_usage`, `search_history` and `sessions` (nothing prunes them).
7. Decide backups and restore drills for the managed database.
