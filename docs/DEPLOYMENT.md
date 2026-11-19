# Deployment

Status: **nothing has been deployed, no image has been built, and no deployment target is configured.**
The only thing that can run today is the seed-backed development API. The full application cannot be
deployed because most of it does not exist yet; see [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).
Everything below that involves Docker is UNVERIFIED (the Docker daemon was not available).

## What runs today

### The development API on the host (verified)

Standard library only, no installs. This is the one path that has been run end to end, including by the
TypeScript client's integration tests.

```bash
cd apps/api
PYTHONPATH=src python3 -m vyron_api.devserver --port 8000
curl -s http://127.0.0.1:8000/api/v1/meta
```

It serves 17 of the 30 routes over 60 seed entities with `wsgiref`, one process, in-memory rate limits.
Every response that contains seed data carries a `data_notice`. It is for development, demos and tests
and must not face the internet.

### Docker Compose (UNVERIFIED)

`docker-compose.yml` has been validated with `docker compose config` for all three profiles, and its
hardening settings are asserted against the resolved model. No container has been started.

```bash
cp .env.example .env                       # then set real passwords; never commit .env
docker compose up -d                       # postgres, neo4j, redis on 127.0.0.1 only
docker compose --profile seed up --build   # the development API on http://127.0.0.1:8000
docker compose --profile app up --build    # api + web: UNVERIFIED, and it cannot work yet (see below)
```

Do not enable the `seed` and `app` profiles together; both APIs publish port 8000.

Things to know before relying on it:

- The data stores start, but **nothing creates the schema**. The migration executor is not written, and
  the PostgreSQL container does not apply the migrations. Applying `0001` to `0005` by hand
  (`psql -f`, in order) is the manual route, also UNVERIFIED.
- The `app` profile's API exits at start-up with `AdapterNotImplementedError` when `DATA_MODE=database`,
  because the PostgreSQL, Neo4j and Redis adapters are not written. The `web` image is a template that
  names files that do not exist.
- The Neo4j health check contains the password in its command, so `docker inspect` shows it. That is
  acceptable for a local container with a local-only password and not for anything shared.
- The `seed-dev` image runs the development server and nothing else. The commands in its Dockerfile
  (`CMD` and `HEALTHCHECK`) were run on the host and behave as expected; the image itself was not built.
- No base image digest is recorded, because none could be verified. Pass one when you can pull images:
  `--build-arg PYTHON_IMAGE=python:3.13-slim@sha256:<digest>`.

## Configuration

All configuration is environment variables, validated at start-up (`config.py`); see `.env.example`.

| Variable | Purpose | Notes |
| --- | --- | --- |
| `APP_ENV` | `development`, `test` or `production` | production is stricter |
| `DATA_MODE` | `seed` or `database` | `database` needs adapters that are not written |
| `ALLOW_SEED_DATA` | allow seed data in production | refused unless `true` |
| `TRUSTED_PROXY_HOPS` | trusted reverse proxies in front of the API | `0` ignores `X-Forwarded-For` |
| `SESSION_SECRET`, `SESSION_SECRET_PREVIOUS` | at least 32 characters; cursor keys derive from it | required in production; rotate by moving the old value to `..._PREVIOUS` |
| `DATABASE_URL`, `POSTGRES_*` | PostgreSQL | schemes `postgresql://` or `postgres://` |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | Neo4j | Neo4j requires a password of at least 8 characters; VYRON does not check |
| `REDIS_URL` | Redis | |
| `GITHUB_TOKEN` | optional GitHub token, no scopes needed for public repositories | empty means 60 requests per hour (each repository read costs 2); a set value is sent only to `api.github.com`, and the development server refuses it on a non-loopback address unless `--allow-token-on-network` is given |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | optional AI layer | empty disables AI; no default model |
| `NEXT_PUBLIC_APP_URL`, `API_INTERNAL_URL` | web app | `NEXT_PUBLIC_*` reaches browsers; never put a secret there |

Generate a session secret with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.

## Health probes

`GET /health/live` answers while the process runs. `GET /health/ready` answers `200` when the service
reports `ok` and `503` otherwise. Today the probes report only the in-memory application; they do not
check PostgreSQL, Neo4j or Redis because no adapter exists to check.

## Intended production shape (a design, with no configuration behind it)

- Web (Next.js) on a platform that supports server rendering; API and workers as one container image;
  managed PostgreSQL, Neo4j (hosted or self-managed) and managed Redis.
- Release order: build and scan the image, run migrations as a separate step under the advisory lock,
  then shift traffic. Migrations are forward-only, so a bad release is rolled back by deploying the
  previous image and writing a new corrective migration, never by editing an applied migration.
- Health probes gate traffic; the API runs several instances behind a proxy that sets `X-Forwarded-For`,
  with `TRUSTED_PROXY_HOPS` set to match.
- Graceful degradation is specified in `docs/ARCHITECTURE.md` section 14 (GitHub, Gemini, Neo4j and Redis
  outages each have a defined behaviour). None of it is implemented.

## Gate before any real deployment

Every item is currently unmet. This list is the definition of "not yet", not a roadmap with dates.

1. Dependencies install and `ruff`, `mypy` and `pytest` pass; the FastAPI binding passes its contract test.
2. The migrations apply to PostgreSQL, the integrity views are empty, and the PostgreSQL executor and
   repositories pass the same tests as the in-memory ones.
3. The Neo4j projector, the Redis cache and rate limiter exist, so more than one API instance behaves
   correctly.
4. The web application builds, passes accessibility and end-to-end tests at desktop and mobile sizes, and
   meets its stated budgets.
5. Authentication exists and has been reviewed, or the deployment is explicitly read-only and public.
6. Images are built, scanned, pinned by digest, and started with the hardened settings.
7. Real data is ingested from real sources with evidence; seed data is absent from the target.
8. Logging, metrics, alerting, backups and a restore drill exist.
9. A dependency audit has been run and CI has actually executed.

## CI

`.github/workflows/ci.yml` defines a job that runs the standard-library checks (`scripts/verify_stdlib.sh
--strict`) and a job for the full Python toolchain. **CI has never run.** Third-party actions are tagged by
version, not pinned by commit hash; pin them before relying on the workflow.
