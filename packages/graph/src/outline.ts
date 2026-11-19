/**
 * The non-visual alternative to the map: the same subgraph as structured text.
 *
 * Screen-reader users, keyboard users who prefer lists, and anyone on a connection too slow for
 * WebGL get exactly the entities and relationships the canvas draws, grouped the way the legend
 * groups them. Nothing here depends on the DOM, so it is tested directly and rendered by a thin
 * component (docs/COMPONENTS.md, "GraphOutline").
 */
import type { Entity, EntityKind, Provenance, Relationship, RelationType, Subgraph } from "../../types/src/api.ts";
import { kindVisual, provenanceVisual, tokens } from "../../ui/src/tokens.ts";
import type { DataGroupKey } from "../../ui/src/tokens.ts";

interface Phrase {
  readonly out: string; // "<this> <out> <other>"
  readonly in: string; // "<this> <in> <other>"
}

/** Total over RelationType: adding a relation type to the API fails the type check until it has words. */
export const RELATION_PHRASES: Readonly<Record<RelationType, Phrase>> = {
  USES: { out: "uses", in: "is used by" },
  DEPENDS_ON: { out: "depends on", in: "is a dependency of" },
  BUILT_ON: { out: "is built on", in: "is a foundation for" },
  RELATED_TO: { out: "is related to", in: "is related to" },
  EVOLVED_FROM: { out: "evolved from", in: "evolved into" },
  REPLACED_BY: { out: "was replaced by", in: "replaced" },
  USED_BY: { out: "is used by", in: "uses" },
  CREATED_BY: { out: "was created by", in: "created" },
  USES_LANGUAGE: { out: "is written in", in: "is the language of" },
  USES_FRAMEWORK: { out: "uses the framework", in: "is a framework used by" },
  OWNED_BY: { out: "is owned by", in: "owns" },
  FORKS: { out: "is a fork of", in: "has a fork" },
  BUILDS: { out: "builds", in: "is built by" },
  OWNS: { out: "owns", in: "is owned by" },
  ACQUIRED: { out: "acquired", in: "was acquired by" },
  INVESTS_IN: { out: "invests in", in: "receives investment from" },
  REFERENCES: { out: "references", in: "is referenced by" },
  RELATES_TO: { out: "relates to", in: "relates to" },
  SUPPORTS: { out: "supports", in: "is supported by" },
  CONTRADICTS: { out: "contradicts", in: "is contradicted by" },
};

export interface OutlineRelation {
  /** A full sentence, for example "Python is used by NumPy." */
  readonly sentence: string;
  readonly otherId: string;
  readonly otherName: string;
  readonly type: RelationType;
  readonly direction: "out" | "in";
  readonly provenance: Provenance;
  readonly provenanceLabel: string;
}

export interface OutlineEntity {
  readonly id: string;
  readonly name: string;
  readonly kind: EntityKind;
  readonly kindLabel: string;
  readonly provenance: Provenance;
  readonly provenanceLabel: string;
  readonly relations: ReadonlyArray<OutlineRelation>;
}

export interface OutlineGroup {
  readonly group: DataGroupKey;
  readonly label: string;
  readonly entities: ReadonlyArray<OutlineEntity>;
}

export interface GraphOutline {
  /** One sentence for `aria-label` / a live region: counts, truncation and the seed notice. */
  readonly summary: string;
  readonly groups: ReadonlyArray<OutlineGroup>;
  readonly entityCount: number;
  readonly relationshipCount: number;
  /** Relationships whose other end was not returned (the node limit cut the graph). */
  readonly omittedRelationships: number;
  readonly truncated: boolean;
}

function compare(a: string, b: string): number {
  const x = a.toLowerCase();
  const y = b.toLowerCase();
  if (x !== y) return x < y ? -1 : 1;
  return a < b ? -1 : a > b ? 1 : 0;
}

const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

export function buildOutline(graph: Pick<Subgraph, "entities" | "relationships" | "truncated" | "data_notice">): GraphOutline {
  const byId = new Map<string, Entity>(graph.entities.map((entity) => [entity.id, entity]));
  const relations = new Map<string, OutlineRelation[]>();
  let omitted = 0;
  let kept = 0;

  const push = (id: string, relation: OutlineRelation): void => {
    const list = relations.get(id);
    if (list) list.push(relation);
    else relations.set(id, [relation]);
  };

  for (const edge of graph.relationships) {
    const source = byId.get(edge.source_id);
    const target = byId.get(edge.target_id);
    if (!source || !target) {
      omitted += 1;
      continue;
    }
    kept += 1;
    push(source.id, relationFor(source, target, edge, "out"));
    push(target.id, relationFor(target, source, edge, "in"));
  }

  const groups = tokens.groupOrder.map((group): OutlineGroup => {
    const entities = graph.entities
      .filter((entity) => kindVisual(entity.kind).group === group)
      .sort((a, b) => compare(a.name, b.name) || compare(a.id, b.id))
      .map((entity): OutlineEntity => {
        const list = (relations.get(entity.id) ?? []).sort(
          (a, b) => compare(a.sentence, b.sentence) || compare(a.otherId, b.otherId),
        );
        return {
          id: entity.id,
          name: entity.name,
          kind: entity.kind,
          kindLabel: kindVisual(entity.kind).label,
          provenance: entity.provenance,
          provenanceLabel: provenanceVisual(entity.provenance).label,
          relations: list,
        };
      });
    return { group, label: tokens.groups[group].label, entities };
  });

  const populated = groups.filter((group) => group.entities.length > 0);
  const parts = [
    `${plural(graph.entities.length, "entity", "entities")} in ${plural(populated.length, "group", "groups")}`,
    plural(kept, "relationship", "relationships"),
  ];
  let summary = `${parts.join(", ")}.`;
  if (graph.truncated) summary += " The view was limited; some connections are not shown.";
  if (graph.data_notice) summary += ` ${graph.data_notice}`;

  return {
    summary,
    groups: populated,
    entityCount: graph.entities.length,
    relationshipCount: kept,
    omittedRelationships: omitted,
    truncated: graph.truncated,
  };
}

function relationFor(self: Entity, other: Entity, edge: Relationship, direction: "out" | "in"): OutlineRelation {
  const phrase = RELATION_PHRASES[edge.type][direction];
  return {
    sentence: `${self.name} ${phrase} ${other.name}.`,
    otherId: other.id,
    otherName: other.name,
    type: edge.type,
    direction,
    provenance: edge.provenance,
    provenanceLabel: provenanceVisual(edge.provenance).label,
  };
}

/** What a screen reader announces when an entity is selected on the map or in the list. */
export function describeSelection(entity: OutlineEntity): string {
  const n = entity.relations.length;
  return `${entity.name}, ${entity.kindLabel.toLowerCase()}. ${plural(n, "connection", "connections")}. ${entity.provenanceLabel}.`;
}
