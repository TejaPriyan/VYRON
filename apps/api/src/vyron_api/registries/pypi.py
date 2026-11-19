"""PyPI registry ingestion driver.

Reads ``https://pypi.org/pypi/<name>/json`` (standard library only) and turns what the package declares into
SOURCED entities and DEPENDS_ON relationships with a recorded Source and Evidence.

What is and is not recorded:

* Only requirements with no marker are recorded as dependencies. A requirement with a marker (``extra ==``,
  ``python_version < "3.10"``, ``sys_platform == "win32"``) depends on something about the installation, and
  evaluating a marker needs a package this project does not install, so such a requirement is counted and
  disclosed in the evidence claim, not recorded as if it were unconditional.
* The name goes into the URL only after it matches the shape of a PyPI name and is put in PyPI's canonical form
  (PEP 503), so the request cannot be steered to another path and no redirect is needed.
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from vyron_api.domain.entities import Entity, EntityKind, Relationship
from vyron_api.domain.evidence import Evidence, Source
from vyron_api.registries.common import (
    REGISTRY_NAMESPACE,
    PackageReference,
    build_package_graph,
    clean_requirement,
    registry_entity_id,
    registry_evidence_id,
    registry_relationship_id,
    registry_source_id,
    slugify,
)
from vyron_api.registries.errors import InvalidPackageNameError, PackageNotFoundError, RegistryError
from vyron_api.registries.http import fetch_json, text_field
from vyron_api.registries.releases import ReleaseList, pypi_release_list

__all__ = [
    "REGISTRY_NAMESPACE",
    "InvalidPackageNameError",
    "PackageNotFoundError",
    "PyPIPackage",
    "fetch_pypi_package",
    "normalize_pypi_name",
    "parse_dependency_name",
    "parse_requirement",
    "pypi_package_to_graph",
    "registry_entity_id",
    "registry_evidence_id",
    "registry_relationship_id",
    "registry_source_id",
    "slugify",
]

PYPI_BASE_URL = "https://pypi.org"
_DEP_NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)")
_AFTER_NAME_RE = re.compile(r"^\s*(?:\[[^\]]*\])?\s*([^;]*)")
_PYPI_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,212}[A-Za-z0-9])?$")
_MAX_REQUIREMENTS = 500


def normalize_pypi_name(name: str) -> str:
    """The canonical form PyPI serves (PEP 503), or ``InvalidPackageNameError`` before any request is made."""
    if not isinstance(name, str) or not _PYPI_NAME_RE.fullmatch(name):
        raise InvalidPackageNameError("not a valid PyPI package name (letters, digits, '.', '-' and '_' only)")
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement(spec: str) -> tuple[str | None, bool]:
    """``(slug, conditional)`` for a PEP 508 requirement; ``conditional`` is true when it carries any marker."""
    cleaned = spec.strip()
    match = _DEP_NAME_RE.match(cleaned)
    if not match:
        return None, False
    try:
        slug = slugify(match.group(1))
    except ValueError:
        return None, False
    return slug, ";" in cleaned


def parse_reference(spec: str) -> PackageReference | None:
    """The package a PEP 508 requirement names, with its version range, or ``None`` if no name can be read.

    A direct reference (``name @ https://...``) names a package but not one the index serves at that name, so it is
    not ``readable``; the URL is never kept.
    """
    cleaned = spec.strip()
    match = _DEP_NAME_RE.match(cleaned)
    if not match:
        return None
    try:
        canonical = normalize_pypi_name(match.group(1))
    except InvalidPackageNameError:
        return None
    rest = _AFTER_NAME_RE.match(cleaned[match.end() :])
    tail = rest.group(1).strip() if rest else ""
    if tail.startswith("@"):
        return PackageReference(canonical, None, False)
    return PackageReference(canonical, clean_requirement(tail) if tail else None, True)


def parse_dependency_name(spec: str, *, include_extras: bool = False) -> str | None:
    """The slug of the package a PEP 508 requirement names; ``None`` for an extra unless ``include_extras``."""
    if not include_extras and re.search(r";\s*extra\s*==", spec.strip()):
        return None
    return parse_requirement(spec)[0]


@dataclass(frozen=True, slots=True)
class PyPIPackage:
    name: str
    slug: str
    version: str
    summary: str
    license_spdx: str | None  # as the maintainer wrote it, one short line; not validated as an SPDX identifier
    author: str | None
    homepage: str | None
    dependencies: tuple[str, ...]  # requirements with no marker
    source_url: str
    conditional_dependencies: tuple[str, ...] = ()  # requirements with a marker: counted, not recorded
    references: tuple[PackageReference, ...] = ()  # the same dependencies by registry name, with their ranges
    left_out: tuple[tuple[str, int], ...] = ()  # (kind, count) for what is declared but not listed
    #: Release dates from the same document; ``None`` when it has no ``releases`` object (PyPI deprecates that key).
    releases: ReleaseList | None = None


def _short_license(info: dict[str, Any]) -> str | None:
    expression = text_field(info, "license_expression", max_chars=100)
    if expression:
        return expression
    text = text_field(info, "license", max_chars=2000)
    return text if text and len(text) <= 64 and "\n" not in text else None  # PyPI often holds the whole text here


def fetch_pypi_package(
    package_name: str,
    *,
    timeout: float = 10.0,
    transport: urllib.request.OpenerDirector | None = None,
    base_url: str = PYPI_BASE_URL,
    allow_local_test_server: bool = False,
) -> PyPIPackage:
    canonical = normalize_pypi_name(package_name)
    url = f"{base_url}/pypi/{canonical}/json"
    data = fetch_json(
        url,
        host="pypi.org",
        what="PyPI",
        timeout=timeout,
        transport=transport,
        allow_local_test_server=allow_local_test_server,
    )
    info = data.get("info") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        raise RegistryError("PyPI sent an answer without package information")
    requires = info.get("requires_dist")
    if requires is None:
        requires = []
    if (
        not isinstance(requires, list)
        or len(requires) > _MAX_REQUIREMENTS
        or not all(isinstance(item, str) for item in requires)
    ):
        raise RegistryError("PyPI sent requirements in an unexpected shape")

    name = text_field(info, "name", max_chars=200) or canonical
    package_slug = slugify(name)
    unconditional: set[str] = set()
    conditional: set[str] = set()
    references: dict[str, PackageReference] = {}
    unusable = 0
    for spec in requires:
        slug, has_marker = parse_requirement(spec)
        reference = parse_reference(spec)
        if slug is None or reference is None:
            unusable += 1
            continue
        if slug != package_slug:
            (conditional if has_marker else unconditional).add(slug)
            if not has_marker:
                references.setdefault(slug, reference)
    only_conditional = conditional - unconditional
    left_out = tuple(
        (kind, count) for kind, count in (("conditional", len(only_conditional)), ("unusable", unusable)) if count
    )
    return PyPIPackage(
        name=name,
        slug=package_slug,
        version=text_field(info, "version", max_chars=100) or "0.0.0",
        summary=text_field(info, "summary", max_chars=500),
        license_spdx=_short_license(info),
        author=text_field(info, "author", max_chars=200) or None,
        homepage=text_field(info, "home_page", max_chars=500) or text_field(info, "project_url", max_chars=500) or None,
        dependencies=tuple(sorted(unconditional)),
        source_url=url,
        conditional_dependencies=tuple(sorted(only_conditional)),
        references=tuple(references[slug] for slug in sorted(references)),
        left_out=left_out,
        releases=pypi_release_list(data.get("releases"), now=datetime.now(UTC)),
    )


def pypi_package_to_graph(
    pkg: PyPIPackage,
    *,
    retrieved_at: datetime | None = None,
    kind: EntityKind = EntityKind.LIBRARY,
) -> tuple[Entity, Source, Evidence, list[Entity], list[Relationship]]:
    """The package, its source, one piece of evidence, its dependency entities and DEPENDS_ON relationships."""
    claim = f"{pkg.name} v{pkg.version} on PyPI declares {len(pkg.dependencies)} dependencies with no condition"
    if pkg.conditional_dependencies:
        claim += (
            f"; {len(pkg.conditional_dependencies)} more apply only under a condition (an extra or an "
            "environment marker) and are not recorded"
        )
    return build_package_graph(
        name=pkg.name,
        slug=pkg.slug,
        kind=kind,
        dependencies=pkg.dependencies,
        source_url=pkg.source_url,
        source_title=f"PyPI: {pkg.name}",
        publisher="Python Package Index",
        claim=claim,
        locator="info.requires_dist",
        retrieved_at=retrieved_at or datetime.now(UTC),
    )
