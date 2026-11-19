"""An in-memory ``PackageProvider`` for HTTP-level tests. Its data is a fixture, not from PyPI or npm."""

from __future__ import annotations

from datetime import UTC, datetime

from vyron_api.providers.registry import (
    PackageCacheStatus,
    PackageRegistry,
    PackageResult,
    PackageSnapshot,
    ReleaseHistory,
    ReleaseResult,
)
from vyron_api.registries.common import PackageReference
from vyron_api.registries.releases import ReleaseRecord


class FakePackageProvider:
    def __init__(self, *, fail_with: Exception | None = None, status: PackageCacheStatus = PackageCacheStatus.FRESH):
        self.fail_with = fail_with
        self.status = status
        self.requested: list[tuple[PackageRegistry, str]] = []

    async def read_package(self, registry: PackageRegistry, name: str) -> PackageResult:
        self.requested.append((registry, name))
        if self.fail_with is not None:
            raise self.fail_with
        snapshot = PackageSnapshot(
            registry=registry,
            name=name,
            version="1.0.0",
            summary="Fixture for tests. This is not data from a registry.",
            license="MIT",
            homepage="https://example.invalid/fixture",
            registry_url=None,
            source_url=f"https://registry.invalid/{name}",
            dependencies=(
                PackageReference("fixture-dep", ">=1.0", True),
                PackageReference("fixture-link", None, False),
            ),
            left_out=(("conditional", 2),),
            fetched_at=datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
        )
        return PackageResult(snapshot, self.status)

    async def read_releases(self, registry: PackageRegistry, name: str) -> ReleaseResult:
        self.requested.append((registry, name))
        if self.fail_with is not None:
            raise self.fail_with
        history = ReleaseHistory(
            registry=registry,
            name=name,
            registry_url=None,
            source_url=f"https://registry.invalid/{name}",
            releases=(
                ReleaseRecord("1.0.0", datetime(2020, 3, 1, 9, 30, tzinfo=UTC), False, "normal"),
                ReleaseRecord("1.1.0rc1", datetime(2021, 6, 15, 8, 0, tzinfo=UTC), True, "normal"),
                ReleaseRecord("1.1.0", datetime(2021, 7, 2, 10, 0, tzinfo=UTC), False, "yanked"),
            ),
            date_meaning="first_file_upload" if registry is PackageRegistry.PYPI else "publish_time",
            omitted_older=4,
            left_out=(("undated", 1),),
            fetched_at=datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
        )
        return ReleaseResult(history, self.status)
