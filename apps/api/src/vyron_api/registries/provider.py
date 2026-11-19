"""The live package provider: one registry request per package, a short cache, and an older copy when it fails.

* A copy read within ``FRESH_SECONDS`` is served without asking the registry again. Past that, the registry is asked.
* When the registry cannot be reached, is limiting requests or sends something unusable, a copy kept from earlier is
  served marked ``stale`` with its original ``fetched_at``. With no copy the error is raised. A package that does not
  exist is never served from the cache.
* At most ``MAX_ENTRIES`` copies are kept (the least recently used go first). Nothing is written anywhere.
* Release dates come out of the same registry document as the dependencies, so one request fills both kinds of copy.
  They are kept in a second, smaller cache (``MAX_HISTORIES``) because a long history is the larger value, and they
  follow the same rules: fresh for ``FRESH_SECONDS``, an older copy marked ``stale`` only for a failure.
* The fetch blocks, so it runs in a worker thread and the event loop is never held.
"""

from __future__ import annotations

import asyncio
import threading
import time
import urllib.request
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

from vyron_api.providers.registry import (
    PackageCacheStatus,
    PackageRegistry,
    PackageResult,
    PackageSnapshot,
    ReleaseHistory,
    ReleaseResult,
)
from vyron_api.registries.errors import InvalidPackageNameError, PackageNotFoundError, RegistryError
from vyron_api.registries.http import LOCAL_TEST_HOSTS
from vyron_api.registries.npm import NPM_BASE_URL, NpmPackage, fetch_npm_package, validate_npm_name
from vyron_api.registries.pypi import PYPI_BASE_URL, PyPIPackage, fetch_pypi_package, normalize_pypi_name
from vyron_api.registries.releases import ReleaseDatesMissingError

FRESH_SECONDS = 600.0
MAX_ENTRIES = 256
MAX_HISTORIES = 64

Fetcher = Callable[[str], PyPIPackage | NpmPackage]


def _homepage(value: str | None) -> str | None:
    """The project's own link as the maintainer wrote it, only if it is a plain http(s) address."""
    if not value or len(value) > 300:
        return None
    parts = urlsplit(value)
    return value if parts.scheme in {"http", "https"} and parts.hostname and not parts.username else None


class LivePackageProvider:
    def __init__(
        self,
        fetchers: dict[PackageRegistry, Fetcher],
        *,
        local_test_server: bool = False,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._fetchers = fetchers
        self._local = local_test_server
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._copies: OrderedDict[tuple[PackageRegistry, str], tuple[PackageSnapshot, float]] = OrderedDict()
        self._histories: OrderedDict[tuple[PackageRegistry, str], tuple[ReleaseHistory, float]] = OrderedDict()

    @staticmethod
    def checked_name(registry: PackageRegistry, name: str) -> str:
        """The name in the form the registry serves, or ``InvalidPackageNameError`` before anything is requested."""
        if registry is PackageRegistry.PYPI:
            return normalize_pypi_name(name)
        return validate_npm_name(name)

    @staticmethod
    def _page(package: PyPIPackage | NpmPackage, canonical: str) -> str:
        """The package's page for people, built from the checked name (never from the registry's answer)."""
        if isinstance(package, PyPIPackage):
            return f"https://pypi.org/project/{canonical}/"
        return f"https://www.npmjs.com/package/{quote(canonical, safe='@/')}"

    def _snapshot(
        self, registry: PackageRegistry, package: PyPIPackage | NpmPackage, canonical: str, fetched_at: datetime
    ) -> PackageSnapshot:
        page = self._page(package, canonical)
        summary = package.summary if isinstance(package, PyPIPackage) else package.description
        license_name = package.license_spdx
        return PackageSnapshot(
            registry=registry,
            name=package.name if registry is PackageRegistry.NPM else canonical,
            version=package.version,
            summary=summary or None,
            license=license_name,
            homepage=_homepage(package.homepage),
            registry_url=None if self._local else page,
            source_url=package.source_url,
            dependencies=package.references,
            left_out=package.left_out,
            fetched_at=fetched_at,
        )

    def _copy(self, key: tuple[PackageRegistry, str]) -> tuple[PackageSnapshot, float] | None:
        with self._lock:
            held = self._copies.get(key)
            if held is not None:
                self._copies.move_to_end(key)
            return held

    def _keep(self, key: tuple[PackageRegistry, str], snapshot: PackageSnapshot) -> None:
        with self._lock:
            self._copies[key] = (snapshot, self._clock())
            self._copies.move_to_end(key)
            while len(self._copies) > MAX_ENTRIES:
                self._copies.popitem(last=False)

    def _history_copy(self, key: tuple[PackageRegistry, str]) -> tuple[ReleaseHistory, float] | None:
        with self._lock:
            held = self._histories.get(key)
            if held is not None:
                self._histories.move_to_end(key)
            return held

    def _keep_history(self, key: tuple[PackageRegistry, str], history: ReleaseHistory) -> None:
        with self._lock:
            self._histories[key] = (history, self._clock())
            self._histories.move_to_end(key)
            while len(self._histories) > MAX_HISTORIES:
                self._histories.popitem(last=False)

    def _remember(
        self, registry: PackageRegistry, canonical: str, package: PyPIPackage | NpmPackage
    ) -> tuple[PackageSnapshot, ReleaseHistory | None]:
        """Keep what one registry answer holds: the dependencies, and the release dates when it has them."""
        key = (registry, canonical)
        fetched_at = self._now()
        snapshot = self._snapshot(registry, package, canonical, fetched_at)
        self._keep(key, snapshot)
        history = None
        if package.releases is not None:
            history = ReleaseHistory(
                registry=registry,
                name=snapshot.name,
                registry_url=snapshot.registry_url,
                source_url=package.source_url,
                releases=package.releases.records,
                date_meaning=package.releases.date_meaning,
                omitted_older=package.releases.omitted_older,
                left_out=package.releases.left_out,
                fetched_at=fetched_at,
            )
            self._keep_history(key, history)
        return snapshot, history

    async def read_package(self, registry: PackageRegistry, name: str) -> PackageResult:
        canonical = self.checked_name(registry, name)
        key = (registry, canonical)
        held = self._copy(key)
        if held is not None and self._clock() - held[1] < FRESH_SECONDS:
            return PackageResult(held[0], PackageCacheStatus.FRESH)
        try:
            package = await asyncio.to_thread(self._fetchers[registry], canonical)
        except (InvalidPackageNameError, PackageNotFoundError):
            raise
        except RegistryError:
            if held is not None:
                return PackageResult(held[0], PackageCacheStatus.STALE)
            raise
        snapshot, _ = self._remember(registry, canonical, package)
        return PackageResult(snapshot, PackageCacheStatus.FRESH)

    async def read_releases(self, registry: PackageRegistry, name: str) -> ReleaseResult:
        canonical = self.checked_name(registry, name)
        key = (registry, canonical)
        held = self._history_copy(key)
        if held is not None and self._clock() - held[1] < FRESH_SECONDS:
            return ReleaseResult(held[0], PackageCacheStatus.FRESH)
        try:
            package = await asyncio.to_thread(self._fetchers[registry], canonical)
        except (InvalidPackageNameError, PackageNotFoundError):
            raise
        except RegistryError:
            if held is not None:
                return ReleaseResult(held[0], PackageCacheStatus.STALE)
            raise
        _, history = self._remember(registry, canonical, package)
        if history is not None:
            return ReleaseResult(history, PackageCacheStatus.FRESH)
        if held is not None:  # the registry answered, but without dates this time: the older copy is all there is
            return ReleaseResult(held[0], PackageCacheStatus.STALE)
        label = "PyPI" if registry is PackageRegistry.PYPI else "npm"
        raise ReleaseDatesMissingError(f"{label} sent no release dates for this package")


def build_live_package_provider(
    *,
    pypi_base_url: str = PYPI_BASE_URL,
    npm_base_url: str = NPM_BASE_URL,
    allow_local_test_server: bool = False,
    timeout: float = 10.0,
    transport: urllib.request.OpenerDirector | None = None,
) -> LivePackageProvider:
    """The provider for real use: ``https://pypi.org`` and ``https://registry.npmjs.org``, in-process cache.

    ``allow_local_test_server`` lets the two base addresses be plain-HTTP loopback addresses (a stand-in run instead of
    the registries). It is never enabled by configuration read from the environment. Without it any other base
    address is refused here, not at the first request.
    """
    for base, real in ((pypi_base_url, PYPI_BASE_URL), (npm_base_url, NPM_BASE_URL)):
        if base == real:
            continue
        parts = urlsplit(base)
        if not (allow_local_test_server and parts.scheme == "http" and parts.hostname in LOCAL_TEST_HOSTS):
            raise ValueError("a registry base address must be the registry's own https address")
        if parts.username or parts.query or parts.fragment:
            raise ValueError("a registry base address must not carry credentials, a query or a fragment")

    def pypi(name: str) -> PyPIPackage:
        return fetch_pypi_package(
            name,
            timeout=timeout,
            transport=transport,
            base_url=pypi_base_url.rstrip("/"),
            allow_local_test_server=allow_local_test_server,
        )

    def npm(name: str) -> NpmPackage:
        return fetch_npm_package(
            name,
            timeout=timeout,
            transport=transport,
            base_url=npm_base_url.rstrip("/"),
            allow_local_test_server=allow_local_test_server,
        )

    return LivePackageProvider(
        {PackageRegistry.PYPI: pypi, PackageRegistry.NPM: npm},
        local_test_server=allow_local_test_server,
    )
