"""What the two registry drivers share: slugs, stable ids, and the shape of the graph data they produce.

Everything the drivers record is ``sourced`` only in the narrow sense that a registry's own metadata says it:
a package declares these dependencies. Nothing here says a package is popular, maintained or safe.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from vyron_api.domain.entities import Entity, EntityKind, Relationship, RelationType
from vyron_api.domain.enums import Reliability, SourceType
from vyron_api.domain.evidence import Evidence, Source
from vyron_api.domain.provenance import Provenance
from vyron_api.domain.rules import check_relationship_endpoints

REGISTRY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "registry.vyron.invalid")

#: Why a declared requirement is not among a package's listed dependencies. ``unusable`` is a name no registry could
#: have issued; the others are kinds of requirement VYRON does not list as dependencies.
LEFT_OUT_KINDS = ("conditional", "development", "peer", "optional", "unusable")
MAX_REQUIREMENT_CHARS = 100
_SAFE_REQUIREMENT = re.compile(r"[A-Za-z0-9.,<>=!~^*+|() -]{1,100}")


@dataclass(frozen=True, slots=True)
class PackageReference:
    """One declared dependency, as the depending package's own metadata states it.

    ``name`` is what to ask the registry for (PyPI's canonical form, npm's exact name). ``requirement`` is the
    version range as written, kept only when it is plain version syntax. ``readable`` is false when the
    requirement points outside the registry (a URL, a path, an alias), because the name then does not identify
    a package there and asking for it would read the wrong thing or nothing.
    """

    name: str
    requirement: str | None
    readable: bool


def clean_requirement(text: str) -> str | None:
    """The version range if it is plain version syntax of at most 100 characters, else ``None``."""
    stripped = text.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        stripped = stripped[1:-1].strip()
    return stripped if _SAFE_REQUIREMENT.fullmatch(stripped) else None


_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Normalize a package name into a valid VYRON slug (a-z0-9 separated by single hyphens)."""
    cleaned = _SLUG_CLEAN.sub("-", name.lower()).strip("-")
    if not cleaned:
        raise ValueError(f"cannot derive valid slug from name {name!r}")
    return cleaned


def registry_entity_id(kind: EntityKind, slug: str) -> str:
    return str(uuid.uuid5(REGISTRY_NAMESPACE, f"{kind.value}/{slug}"))


def registry_source_id(url: str) -> str:
    return str(uuid.uuid5(REGISTRY_NAMESPACE, f"source/{url}"))


def registry_evidence_id(entity_id: str, source_id: str) -> str:
    return str(uuid.uuid5(REGISTRY_NAMESPACE, f"evidence/{entity_id}/{source_id}"))


def registry_relationship_id(source_id: str, rel: RelationType, target_id: str) -> str:
    return str(uuid.uuid5(REGISTRY_NAMESPACE, f"rel/{source_id}/{rel.value}/{target_id}"))


def build_package_graph(
    *,
    name: str,
    slug: str,
    kind: EntityKind,
    dependencies: tuple[str, ...],
    source_url: str,
    source_title: str,
    publisher: str,
    claim: str,
    locator: str,
    retrieved_at: datetime,
) -> tuple[Entity, Source, Evidence, list[Entity], list[Relationship]]:
    """The package, the registry page it came from, one piece of evidence, and its DEPENDS_ON edges."""
    main = Entity(id=registry_entity_id(kind, slug), kind=kind, slug=slug, name=name, provenance=Provenance.SOURCED)
    source = Source(
        id=registry_source_id(source_url),
        url=source_url,
        title=source_title,
        source_type=SourceType.OTHER,  # a registry's JSON API is not documentation, a repository or a paper
        retrieved_at=retrieved_at,
        publisher=publisher,
        reliability=Reliability.PRIMARY,  # the maintainers' own declaration of what the package needs
    )
    evidence = Evidence(
        id=registry_evidence_id(main.id, source.id),
        claim=claim,
        source_id=source.id,
        entity_id=main.id,
        retrieved_at=retrieved_at,
        locator=locator,
    )
    dependency_entities: list[Entity] = []
    relationships: list[Relationship] = []
    for dep_slug in dependencies:
        dep = Entity(
            id=registry_entity_id(EntityKind.LIBRARY, dep_slug),
            kind=EntityKind.LIBRARY,
            slug=dep_slug,
            name=dep_slug,
            provenance=Provenance.SOURCED,
        )
        check_relationship_endpoints(RelationType.DEPENDS_ON, kind, EntityKind.LIBRARY)
        dependency_entities.append(dep)
        relationships.append(
            Relationship(
                source_id=main.id,
                target_id=dep.id,
                type=RelationType.DEPENDS_ON,
                provenance=Provenance.SOURCED,
                evidence_ids=(evidence.id,),
            )
        )
    return main, source, evidence, dependency_entities, relationships
