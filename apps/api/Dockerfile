# syntax=docker/dockerfile:1
#
# VYRON API image. Build context is the REPOSITORY ROOT:
#   docker build -f apps/api/Dockerfile --target seed-dev -t vyron-api:seed-dev .
#   docker build -f apps/api/Dockerfile --target runtime  -t vyron-api:runtime  .
#
# STATUS: UNVERIFIED. The Docker daemon was not available when this was written, so no image has been
# built or run. The Dockerfile is checked statically by tests/test_docker_config.py (structure and
# hardening rules only). The commands each target runs were run directly on the host where possible.
#
# Two targets:
#   seed-dev  Standard library only, no pip install, no network at build time. Runs the development
#             server over the in-memory seed (the same command the host test suite exercises). DEVELOPMENT
#             ONLY: single process, wsgiref, seed data.
#   runtime   The FastAPI application with its declared dependencies (UNVERIFIED: they could not be
#             installed here). With DATA_MODE=database it exits at start-up with AdapterNotImplementedError
#             because the PostgreSQL, Neo4j and Redis adapters are not written yet.
#
# Pin the base image by digest when you can pull it (docker buildx imagetools inspect python:3.13-slim),
# for example --build-arg PYTHON_IMAGE=python:3.13-slim@sha256:<digest>. No digest is recorded here
# because none could be verified.

ARG PYTHON_IMAGE=python:3.13-slim

# ---------------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
# A fixed non-root identity. The numeric uid lets orchestrators verify "runs as non-root".
RUN groupadd --system --gid 10001 vyron \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin vyron
WORKDIR /app

# ---------------------------------------------------------------------------------------------------
FROM base AS seed-dev
COPY apps/api/src /app/src
ENV PYTHONPATH=/app/src \
    APP_ENV=development \
    DATA_MODE=seed
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"]
CMD ["python", "-m", "vyron_api.devserver", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------------------------------------------
FROM base AS build
COPY apps/api/pyproject.toml /build/pyproject.toml
COPY apps/api/src /build/src
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install /build

# ---------------------------------------------------------------------------------------------------
FROM base AS runtime
COPY --from=build /opt/venv /opt/venv
COPY infrastructure/database/migrations /app/migrations
ENV PATH=/opt/venv/bin:$PATH \
    APP_ENV=production
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "--factory", "vyron_api.fastapi_app:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
