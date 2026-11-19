"""Registry ingestion service: puts one package's declared dependencies into a graph with their evidence.

Rules it keeps:

* Every ``sourced`` entity it adds has evidence of its own (the integrity view
  ``integrity_sourced_entities_without_evidence`` would flag one that does not), and every relationship points at
  stored evidence.
* An entity that is already in the graph is reused, never replaced and never upgraded: a seed entity stays a seed
  entity. Evidence about it is attached to the entity that is actually in the graph.
* Nothing is stored anywhere but the graph it is given. There is no database write here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from vyron_api.adapters.memory_graph import InMemoryGraph
from vyron_api.domain.entities import Entity, EntityKind, Relationship
from vyron_api.domain.evidence import Evidence, Source
from vyron_api.registries.common import registry_evidence_id
from vyron_api.registries.npm import NpmPackage, fetch_npm_package, npm_package_to_graph
from vyron_api.registries.pypi import PyPIPackage, fetch_pypi_package, pypi_package_to_graph


@dataclass(frozen=True, slots=True)
class IngestResult:
    main_entity: Entity
    source: Source
    evidence: Evidence  # about main_entity as it is in the graph
    dependencies: list[Entity]
    relationships: list[Relationship]
    new_entities_count: int
    new_relationships_count: int


class RegistryIngestService:
    def __init__(self, graph: InMemoryGraph | None = None) -> None:
        self.graph = graph if graph is not None else InMemoryGraph()
        self.sources: dict[str, Source] = {}
        self.evidence: dict[str, Evidence] = {}

    def _apply_to_graph(
        self,
        main_entity: Entity,
        source: Source,
        evidence: Evidence,
        dep_entities: list[Entity],
        relationships: list[Relationship],
    ) -> IngestResult:
        self.sources[source.id] = source
        new_entities = 0
        new_relations = 0

        active_main = self.graph.find_entity(main_entity.kind, main_entity.slug)
        if active_main is None:
            self.graph.add_entity(main_entity)
            active_main = main_entity
            new_entities += 1
        if active_main.id != evidence.entity_id:
            # The entity was already here under another id (a seed one, say): the evidence is about that one.
            evidence = replace(evidence, id=registry_evidence_id(active_main.id, source.id), entity_id=active_main.id)
        self.evidence[evidence.id] = evidence

        active_by_original: dict[str, Entity] = {}
        for dep in dep_entities:
            active_dep = self.graph.find_entity(dep.kind, dep.slug)
            if active_dep is None:
                self.graph.add_entity(dep)
                active_dep = dep
                new_entities += 1
                own = Evidence(
                    id=registry_evidence_id(dep.id, source.id),
                    claim=f"{dep.name} is listed as a dependency of {active_main.name} in {source.title}",
                    source_id=source.id,
                    entity_id=dep.id,
                    retrieved_at=evidence.retrieved_at,
                    locator=evidence.locator,
                )
                self.evidence[own.id] = own
            active_by_original[dep.id] = active_dep

        active_relationships: list[Relationship] = []
        for rel in relationships:
            target = active_by_original[rel.target_id]
            wired = Relationship(
                source_id=active_main.id,
                target_id=target.id,
                type=rel.type,
                provenance=rel.provenance,
                evidence_ids=(evidence.id,),
            )
            if not self.graph.has_relationship(wired.source_id, wired.target_id, wired.type):
                self.graph.add_relationship(wired)
                new_relations += 1
            active_relationships.append(wired)

        return IngestResult(
            main_entity=active_main,
            source=source,
            evidence=evidence,
            dependencies=list(active_by_original.values()),
            relationships=active_relationships,
            new_entities_count=new_entities,
            new_relationships_count=new_relations,
        )

    def ingest_pypi(
        self,
        package_name: str,
        *,
        kind: EntityKind = EntityKind.LIBRARY,
        retrieved_at: datetime | None = None,
        pkg: PyPIPackage | None = None,
    ) -> IngestResult:
        package = pkg if pkg is not None else fetch_pypi_package(package_name)
        data = pypi_package_to_graph(package, retrieved_at=retrieved_at or datetime.now(UTC), kind=kind)
        return self._apply_to_graph(*data)

    def ingest_npm(
        self,
        package_name: str,
        *,
        kind: EntityKind = EntityKind.LIBRARY,
        retrieved_at: datetime | None = None,
        pkg: NpmPackage | None = None,
    ) -> IngestResult:
        package = pkg if pkg is not None else fetch_npm_package(package_name)
        data = npm_package_to_graph(package, retrieved_at=retrieved_at or datetime.now(UTC), kind=kind)
        return self._apply_to_graph(*data)
