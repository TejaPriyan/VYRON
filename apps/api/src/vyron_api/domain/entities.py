"""Core graph vocabulary shared by PostgreSQL, Neo4j and the API.

PostgreSQL is authoritative for these records; Neo4j holds a rebuildable projection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from vyron_api.domain.provenance import Provenance

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_ALIASES = 50  # the API contract bounds this array too


class EntityKind(StrEnum):
    TECHNOLOGY = "technology"
    LANGUAGE = "language"
    FRAMEWORK = "framework"
    LIBRARY = "library"
    AI_MODEL = "ai_model"
    COMPANY = "company"
    ORGANIZATION = "organization"
    REPOSITORY = "repository"
    DATASET = "dataset"
    RESEARCH_PAPER = "research_paper"
    EVENT = "event"
    API = "api"
    DATABASE = "database"
    PLATFORM = "platform"


class RelationType(StrEnum):
    # technology
    USES = "USES"
    DEPENDS_ON = "DEPENDS_ON"
    BUILT_ON = "BUILT_ON"
    RELATED_TO = "RELATED_TO"
    EVOLVED_FROM = "EVOLVED_FROM"
    REPLACED_BY = "REPLACED_BY"
    USED_BY = "USED_BY"
    CREATED_BY = "CREATED_BY"
    # repository
    USES_LANGUAGE = "USES_LANGUAGE"
    USES_FRAMEWORK = "USES_FRAMEWORK"
    OWNED_BY = "OWNED_BY"
    FORKS = "FORKS"
    # company
    BUILDS = "BUILDS"
    OWNS = "OWNS"
    ACQUIRED = "ACQUIRED"
    INVESTS_IN = "INVESTS_IN"
    # research
    REFERENCES = "REFERENCES"
    RELATES_TO = "RELATES_TO"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"


@dataclass(frozen=True, slots=True)
class Entity:
    id: str
    kind: EntityKind
    slug: str
    name: str
    provenance: Provenance
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("entity id must not be empty")
        if not _SLUG_RE.fullmatch(self.slug):
            raise ValueError(f"invalid entity slug: {self.slug!r}")
        if not self.name.strip():
            raise ValueError("entity name must not be empty")
        if len(self.aliases) > MAX_ALIASES:
            raise ValueError(f"an entity may have at most {MAX_ALIASES} aliases")
        for alias in self.aliases:
            if not alias.strip() or len(alias) > 80:
                raise ValueError(f"invalid alias: {alias!r}")


@dataclass(frozen=True, slots=True)
class Relationship:
    source_id: str
    target_id: str
    type: RelationType
    provenance: Provenance
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source_id == self.target_id:
            raise ValueError("a relationship cannot connect an entity to itself")
        if self.provenance.requires_evidence and not self.evidence_ids:
            raise ValueError("a 'sourced' relationship must reference at least one evidence id")
