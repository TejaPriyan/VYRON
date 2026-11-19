"""Composition of the seed-backed development application.

This wires the in-memory adapters and the development seed. It is for local development, demos and
tests; it is NOT the production composition, which will use the PostgreSQL and Neo4j adapters (not
written yet). Responses from this application carry the seed-data notice.
"""

from __future__ import annotations

import secrets
from collections.abc import Collection, Sequence

from vyron_api.adapters.memory_timeline import InMemoryTimeline
from vyron_api.api.app import Application
from vyron_api.api.cursor import CursorCodec
from vyron_api.api.ratelimit import FixedWindowRateLimiter
from vyron_api.config import DataMode, Settings
from vyron_api.github.provider import build_live_provider
from vyron_api.providers.github import GitHubProvider
from vyron_api.providers.registry import PackageProvider
from vyron_api.registries.provider import build_live_package_provider
from vyron_api.seed.dev_seed import build_seed_graph, seed_timeline
from vyron_api.services.catalog import CatalogService


class AdapterNotImplementedError(NotImplementedError):
    """The configured data mode needs adapters that have not been written (or cannot be verified)."""


def build_application(settings: Settings) -> Application:
    """The application for validated ``settings``."""
    if settings.data_mode is DataMode.SEED:
        token = settings.github_token.reveal() if settings.github_token else None
        return build_seed_application(
            cursor_secrets=settings.cursor_secrets(),
            github=build_live_provider(token=token),
            packages=build_live_package_provider(),
        )
    raise AdapterNotImplementedError(
        "DATA_MODE=database needs the PostgreSQL, Neo4j and Redis adapters, which are not "
        "implemented yet (UNVERIFIED: their drivers cannot be installed in the build environment)"
    )


def build_seed_application(
    *,
    cursor_secrets: Sequence[bytes] | None = None,
    rate_limit: bool = True,
    github: GitHubProvider | None = None,
    packages: PackageProvider | None = None,
    allowed_hosts: Collection[str] | None = None,
) -> Application:
    """An ``Application`` over the development seed.

    Without ``cursor_secrets`` a random per-process secret is used, so cursors stop working after a
    restart (acceptable for development; production must supply stable secrets from the environment).
    Without ``github`` the two GitHub routes answer 503; with one they read live data, which is real
    even though the graph around it is seed data. The package route works the same way: without ``packages`` it
    answers 503, with one it reads the registry live.
    """
    graph = build_seed_graph()
    service = CatalogService(
        graph,
        graph,
        InMemoryTimeline(seed_timeline()),
        CursorCodec(list(cursor_secrets) if cursor_secrets else [secrets.token_bytes(32)]),
        analytics=graph,
        github=github,
        packages=packages,
        data_mode="seed",
    )
    return Application(
        service,
        rate_limiter=FixedWindowRateLimiter() if rate_limit else None,
        allowed_hosts=allowed_hosts,
    )
