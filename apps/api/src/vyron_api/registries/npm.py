"""npm registry ingestion driver.

Reads ``https://registry.npmjs.org/<name>`` (standard library only) and turns what the package's ``latest``
version declares into SOURCED entities and DEPENDS_ON relationships with a recorded Source and Evidence.

Only the runtime ``dependencies`` of the version tagged ``latest`` are recorded. Development, peer and optional
dependencies are not, and ``latest`` is the maintainers' tag, not necessarily the highest version number. The
name goes into the URL only after it matches the shape of an npm name.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from vyron_api.domain.entities import Entity, EntityKind, Relationship
from vyron_api.domain.evidence import Evidence, Source
from vyron_api.registries.common import PackageReference, build_package_graph, clean_requirement, slugify
from vyron_api.registries.errors import InvalidPackageNameError, RegistryError
from vyron_api.registries.http import fetch_json, text_field
from vyron_api.registries.releases import ReleaseList, npm_release_list

NPM_BASE_URL = "https://registry.npmjs.org"
# Requirements that point outside the registry: an alias, a URL, a path, a git or hosting-service shorthand.
_OUTSIDE_REGISTRY = re.compile(
    r"^(?:npm:|git[+:@]|https?:|file:|link:|workspace:|github:|gitlab:|bitbucket:|gist:|\.{1,2}/|/|~/)|"
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:#.*)?$",
    re.IGNORECASE,
)
_NPM_NAME_RE = re.compile(r"^(?:@[A-Za-z0-9~-][A-Za-z0-9._~-]*/)?[A-Za-z0-9~-][A-Za-z0-9._~-]*$")
_MAX_NAME_CHARS = 214
_MAX_DEPENDENCIES = 500


def validate_npm_name(name: str) -> str:
    """The name unchanged if it has the shape of an npm name (scoped or not), else ``InvalidPackageNameError``."""
    if not isinstance(name, str) or len(name) > _MAX_NAME_CHARS or not _NPM_NAME_RE.fullmatch(name):
        raise InvalidPackageNameError("not a valid npm package name")
    return name


@dataclass(frozen=True, slots=True)
class NpmPackage:
    name: str
    slug: str
    version: str
    description: str
    license_spdx: str | None  # as the maintainer wrote it; not validated as an SPDX identifier
    homepage: str | None
    dependencies: tuple[str, ...]
    source_url: str
    references: tuple[PackageReference, ...] = ()  # the same dependencies by exact name, with their ranges
    left_out: tuple[tuple[str, int], ...] = ()  # (kind, count) for what is declared but not listed
    releases: ReleaseList | None = None  # dates from the same document's ``time``; None when it has none


def npm_reference(name: str, requirement: object) -> PackageReference | None:
    """One entry of ``dependencies``, or ``None`` when the name is not one npm could have issued."""
    try:
        validate_npm_name(name)
    except InvalidPackageNameError:
        return None
    text = requirement.strip() if isinstance(requirement, str) else ""
    if _OUTSIDE_REGISTRY.search(text):
        return PackageReference(name, None, False)
    return PackageReference(name, clean_requirement(text) if text else None, True)


def _declared_count(version_data: dict[str, object], key: str) -> int:
    value = version_data.get(key)
    return len(value) if isinstance(value, dict) else 0


def fetch_npm_package(
    package_name: str,
    *,
    timeout: float = 10.0,
    transport: urllib.request.OpenerDirector | None = None,
    base_url: str = NPM_BASE_URL,
    allow_local_test_server: bool = False,
) -> NpmPackage:
    validate_npm_name(package_name)
    url = f"{base_url}/{urllib.parse.quote(package_name, safe='@')}"  # '@scope/name' -> '@scope%2Fname'
    data = fetch_json(
        url,
        host="registry.npmjs.org",
        what="npm",
        timeout=timeout,
        transport=transport,
        allow_local_test_server=allow_local_test_server,
    )
    if not isinstance(data, dict):
        raise RegistryError("npm sent an answer that is not a package document")
    dist_tags = data.get("dist-tags")
    versions = data.get("versions")
    latest = dist_tags.get("latest") if isinstance(dist_tags, dict) else None
    if not isinstance(latest, str) or not isinstance(versions, dict):
        raise RegistryError("npm sent a package document without a latest version")
    version_data = versions.get(latest)
    if not isinstance(version_data, dict):
        raise RegistryError("npm sent a package document without a latest version")
    raw_deps = version_data.get("dependencies", {})
    if raw_deps is None:
        raw_deps = {}
    if not isinstance(raw_deps, dict) or len(raw_deps) > _MAX_DEPENDENCIES:
        raise RegistryError("npm sent dependencies in an unexpected shape")

    name = text_field(data, "name", max_chars=_MAX_NAME_CHARS) or package_name
    package_slug = slugify(name)
    deps: set[str] = set()
    references: list[PackageReference] = []
    unusable = 0
    for dep_name, requirement in raw_deps.items():
        try:
            dep_slug = slugify(dep_name) if isinstance(dep_name, str) else None
        except ValueError:
            dep_slug = None
        reference = npm_reference(dep_name, requirement) if isinstance(dep_name, str) else None
        if dep_slug is None or reference is None:
            unusable += 1
            continue
        if dep_slug != package_slug:
            deps.add(dep_slug)
            references.append(reference)
    left_out = tuple(
        (kind, count)
        for kind, count in (
            ("development", _declared_count(version_data, "devDependencies")),
            ("peer", _declared_count(version_data, "peerDependencies")),
            ("optional", _declared_count(version_data, "optionalDependencies")),
            ("unusable", unusable),
        )
        if count
    )

    raw_license = data.get("license") or version_data.get("license")
    if isinstance(raw_license, dict):
        raw_license = raw_license.get("type")
    license_text = raw_license.strip() if isinstance(raw_license, str) else ""
    return NpmPackage(
        name=name,
        slug=package_slug,
        version=latest[:100],
        description=(
            text_field(data, "description", max_chars=500) or text_field(version_data, "description", max_chars=500)
        ),
        license_spdx=license_text[:64] or None,
        homepage=(
            text_field(data, "homepage", max_chars=500) or text_field(version_data, "homepage", max_chars=500) or None
        ),
        dependencies=tuple(sorted(deps)),
        source_url=url,
        references=tuple(sorted(references, key=lambda ref: ref.name)),
        left_out=left_out,
        releases=npm_release_list(data.get("time"), versions, now=datetime.now(UTC)),
    )


def npm_package_to_graph(
    pkg: NpmPackage,
    *,
    retrieved_at: datetime | None = None,
    kind: EntityKind = EntityKind.LIBRARY,
) -> tuple[Entity, Source, Evidence, list[Entity], list[Relationship]]:
    """The package, its source, one piece of evidence, its dependency entities and DEPENDS_ON relationships."""
    return build_package_graph(
        name=pkg.name,
        slug=pkg.slug,
        kind=kind,
        dependencies=pkg.dependencies,
        source_url=pkg.source_url,
        source_title=f"npm: {pkg.name}",
        publisher="npm Registry",
        claim=(
            f"{pkg.name} v{pkg.version} on npm declares {len(pkg.dependencies)} runtime dependencies "
            "(development, peer and optional dependencies are not recorded)"
        ),
        locator=f"versions.{pkg.version}.dependencies",
        retrieved_at=retrieved_at or datetime.now(UTC),
    )
