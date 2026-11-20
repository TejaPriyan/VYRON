"""Which entity kinds each relationship type may connect.

These rules keep the graph meaningful (a repository cannot "ACQUIRE" a language) and are enforced
by the graph store, the seed validator and, later, the ingestion pipeline. ``RELATED_TO`` is the
deliberately weak, unrestricted relation.
"""

from __future__ import annotations

from vyron_api.domain.entities import EntityKind as K
from vyron_api.domain.entities import RelationType as R

_TECH = frozenset({K.TECHNOLOGY, K.LANGUAGE, K.FRAMEWORK, K.LIBRARY, K.DATABASE, K.PLATFORM, K.API, K.AI_MODEL})
_ORG = frozenset({K.COMPANY, K.ORGANIZATION})
_REPO = frozenset({K.REPOSITORY})
_ANY = frozenset(K)

ENDPOINT_RULES: dict[R, tuple[frozenset[K], frozenset[K]]] = {
    R.USES: (_TECH | _REPO | _ORG, _TECH),
    R.DEPENDS_ON: (_TECH | _REPO, _TECH | _REPO),
    R.BUILT_ON: (_TECH, _TECH),
    R.RELATED_TO: (_ANY, _ANY),
    R.EVOLVED_FROM: (_TECH | _REPO, _TECH | _REPO),
    R.REPLACED_BY: (_TECH, _TECH),
    R.USED_BY: (_TECH, _TECH | _REPO | _ORG),
    R.CREATED_BY: (_TECH | _REPO | frozenset({K.DATASET}), _ORG),
    R.USES_LANGUAGE: (_REPO, frozenset({K.LANGUAGE})),
    R.USES_FRAMEWORK: (_REPO, frozenset({K.FRAMEWORK, K.LIBRARY})),
    R.OWNED_BY: (_REPO, _ORG),
    R.FORKS: (_REPO, _REPO),
    R.BUILDS: (_ORG, _TECH | _REPO),
    R.OWNS: (_ORG, _ORG | _TECH | _REPO),
    R.ACQUIRED: (_ORG, _ORG),
    R.INVESTS_IN: (_ORG, _ORG),
    R.REFERENCES: (frozenset({K.RESEARCH_PAPER}), _ANY),
    R.RELATES_TO: (frozenset({K.RESEARCH_PAPER}), _ANY),
    R.SUPPORTS: (frozenset({K.RESEARCH_PAPER}), _ANY),
    R.CONTRADICTS: (frozenset({K.RESEARCH_PAPER}), _ANY),
}


class InvalidRelationshipError(ValueError):
    """The relationship type does not allow these endpoint kinds."""


def check_relationship_endpoints(rel_type: R, source_kind: K, target_kind: K) -> None:
    sources, targets = ENDPOINT_RULES[rel_type]
    if source_kind not in sources:
        raise InvalidRelationshipError(f"{rel_type.value} cannot start at a {source_kind.value} entity")
    if target_kind not in targets:
        raise InvalidRelationshipError(f"{rel_type.value} cannot end at a {target_kind.value} entity")
