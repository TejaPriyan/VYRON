"""What the API needs from a package registry: one package's declared dependencies and release dates, and where
they were read.

The values are the registry's own, as its metadata states them. Nothing here says a package is popular, maintained
or safe, and nothing is stored beyond a short in-process cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from vyron_api.registries.common import PackageReference
from vyron_api.registries.releases import ReleaseRecord


class PackageRegistry(StrEnum):
    PYPI = "pypi"
    NPM = "npm"


class PackageCacheStatus(StrEnum):
    """How current a package read is. Always shown with ``fetched_at`` so a reader can judge it."""

    FRESH = "fresh"  # read from the registry just now, or from a copy read within the freshness window
    STALE = "stale"  # the registry could not be read, so an older copy is shown


@dataclass(frozen=True, slots=True)
class PackageSnapshot:
    registry: PackageRegistry
    #: The name as the registry has it: PyPI's canonical form, npm's exact name.
    name: str
    version: str
    summary: str | None
    license: str | None
    homepage: str | None
    #: The package's page for people (pypi.org/project/..., npmjs.com/package/...), built from the checked name.
    #: ``None`` when the read came from a local stand-in, because that page would be a different, real package.
    registry_url: str | None
    #: The registry API address the values were read from: the evidence for the label.
    source_url: str
    dependencies: tuple[PackageReference, ...]
    #: ``(kind, count)`` for what the package declares but this read does not list; kinds are in ``LEFT_OUT_KINDS``.
    left_out: tuple[tuple[str, int], ...]
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class PackageResult:
    snapshot: PackageSnapshot
    cache_status: PackageCacheStatus


@dataclass(frozen=True, slots=True)
class ReleaseHistory:
    """A package's release dates as its registry records them. ``date_meaning`` says what a date is (see
    ``registries/releases.py``): it differs by registry and is never presented as one thing."""

    registry: PackageRegistry
    name: str
    registry_url: str | None  # ``None`` when the read came from a local stand-in, as for ``PackageSnapshot``
    source_url: str
    releases: tuple[ReleaseRecord, ...]  # oldest first
    date_meaning: str
    omitted_older: int  # earlier releases left out to keep the list bounded
    left_out: tuple[tuple[str, int], ...]  # (kind, count); kinds are in ``RELEASE_LEFT_OUT_KINDS``
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class ReleaseResult:
    history: ReleaseHistory
    cache_status: PackageCacheStatus


class PackageProvider(Protocol):
    async def read_package(self, registry: PackageRegistry, name: str) -> PackageResult:
        """Read one package's declared dependencies.

        Raises ``InvalidPackageNameError`` before any request when the name is not one the registry could have issued,
        ``PackageNotFoundError`` for an unknown package, and ``RegistryError`` (or its rate-limit subclass) when the
        registry cannot be read and there is no older copy to show.
        """
        ...

    async def read_releases(self, registry: PackageRegistry, name: str) -> ReleaseResult:
        """Read one package's release dates, from the same registry document as ``read_package``.

        Raises what ``read_package`` raises, and ``ReleaseDatesMissingError`` when the registry answered without release
        dates and there is no older copy to show.
        """
        ...
