/**
 * The map's data model: entities, relationships and clusters indexed for drawing and lookup.
 *
 * Built from what the API returns (`graph/overview`, `entities`, `entities/{id}/neighbors`), never
 * from invented data. Nothing is dropped silently: an entity the API did not place in a cluster goes
 * to a catch-all group, and a relationship whose end is missing is counted in `omittedRelationships`.
 */
import type { Entity, EntityKind, GraphOverview, Relationship } from "../../types/src/api.ts";
import { kindVisual, provenanceVisual, tokens } from "../../ui/src/tokens.ts";
import type { ProvenanceStyleKey } from "../../ui/src/tokens.ts";

export const UNCLUSTERED_ID = "unclustered";

export interface AtlasCluster {
  readonly id: string;
  readonly label: string;
  /** Members in the whole graph, as the API counted them. */
  readonly size: number;
  /** Members that are loaded here, most central first. */
  readonly memberIds: ReadonlyArray<string>;
  /** False for the catch-all group, which is not a community the API detected. */
  readonly detected: boolean;
}

export interface Connection {
  readonly otherId: string;
  readonly relationship: Relationship;
  /** `out`: this entity is the source; `in`: it is the target. */
  readonly direction: "out" | "in";
}

/** Relationships between two clusters, counted. */
export interface ClusterLink {
  readonly a: string;
  readonly b: string;
  readonly count: number;
  /** The line style shared by every underlying relationship, or `derived` when they differ. */
  readonly style: ProvenanceStyleKey;
}

export interface AtlasInput {
  readonly entities: ReadonlyArray<Entity>;
  readonly relationships: ReadonlyArray<Relationship>;
  readonly clusters: GraphOverview["clusters"];
  /** PageRank by entity id; may be partial. */
  readonly scores: ReadonlyMap<string, number>;
  readonly notice: string | null;
}

export interface Atlas {
  /** Sorted by name, then id. */
  readonly entities: ReadonlyArray<Entity>;
  readonly byId: ReadonlyMap<string, Entity>;
  readonly relationships: ReadonlyArray<Relationship>;
  readonly omittedRelationships: number;
  readonly clusters: ReadonlyArray<AtlasCluster>;
  readonly clusterOf: ReadonlyMap<string, string>;
  readonly clusterLinks: ReadonlyArray<ClusterLink>;
  readonly connections: ReadonlyMap<string, ReadonlyArray<Connection>>;
  readonly kindCounts: ReadonlyArray<{ readonly kind: EntityKind; readonly count: number }>;
  readonly notice: string | null;
  /** What `importance` is made of: PageRank when every entity has a score, otherwise the connection count. */
  readonly importanceMethod: "pagerank" | "connections";
  /** 0 to 1: PageRank relative to the highest score when every entity has one, otherwise degree. */
  importance(id: string): number;
}

const compareText = (a: string, b: string): number => {
  const x = a.toLowerCase();
  const y = b.toLowerCase();
  if (x !== y) return x < y ? -1 : 1;
  return a < b ? -1 : a > b ? 1 : 0;
};

const relationshipKey = (r: Relationship): string => `${r.source_id}|${r.type}|${r.target_id}`;

export function buildAtlas(input: AtlasInput): Atlas {
  const byId = new Map<string, Entity>();
  for (const entity of input.entities) if (!byId.has(entity.id)) byId.set(entity.id, entity);
  const entities = [...byId.values()].sort((a, b) => compareText(a.name, b.name) || compareText(a.id, b.id));

  const seen = new Set<string>();
  const relationships: Relationship[] = [];
  let omitted = 0;
  for (const relationship of input.relationships) {
    const key = relationshipKey(relationship);
    if (seen.has(key)) continue;
    seen.add(key);
    if (!byId.has(relationship.source_id) || !byId.has(relationship.target_id)) {
      omitted += 1;
      continue;
    }
    relationships.push(relationship);
  }
  relationships.sort((a, b) => compareText(relationshipKey(a), relationshipKey(b)));

  const clusterOf = new Map<string, string>();
  const clusters: AtlasCluster[] = [];
  for (const cluster of input.clusters) {
    const memberIds = cluster.member_ids.filter((id) => byId.has(id) && !clusterOf.has(id));
    if (memberIds.length === 0) continue;
    for (const id of memberIds) clusterOf.set(id, cluster.id);
    clusters.push({ id: cluster.id, label: cluster.label, size: cluster.size, memberIds, detected: true });
  }
  const loose = entities.filter((entity) => !clusterOf.has(entity.id)).map((entity) => entity.id);
  if (loose.length > 0) {
    for (const id of loose) clusterOf.set(id, UNCLUSTERED_ID);
    clusters.push({ id: UNCLUSTERED_ID, label: "Other", size: loose.length, memberIds: loose, detected: false });
  }

  const connections = new Map<string, Connection[]>();
  const push = (id: string, connection: Connection): void => {
    const list = connections.get(id);
    if (list) list.push(connection);
    else connections.set(id, [connection]);
  };
  for (const relationship of relationships) {
    push(relationship.source_id, { otherId: relationship.target_id, relationship, direction: "out" });
    push(relationship.target_id, { otherId: relationship.source_id, relationship, direction: "in" });
  }
  const nameOf = (id: string): string => byId.get(id)?.name ?? id;
  for (const list of connections.values()) {
    list.sort((a, b) => compareText(nameOf(a.otherId), nameOf(b.otherId)) || compareText(a.relationship.type, b.relationship.type));
  }

  const pairs = new Map<string, { a: string; b: string; count: number; styles: Set<ProvenanceStyleKey> }>();
  for (const relationship of relationships) {
    const a = clusterOf.get(relationship.source_id);
    const b = clusterOf.get(relationship.target_id);
    if (a === undefined || b === undefined || a === b) continue;
    const [first, second] = a < b ? [a, b] : [b, a];
    const key = `${first}|${second}`;
    const entry = pairs.get(key) ?? { a: first, b: second, count: 0, styles: new Set<ProvenanceStyleKey>() };
    entry.count += 1;
    entry.styles.add(provenanceVisual(relationship.provenance).styleClass);
    pairs.set(key, entry);
  }
  const clusterLinks: ClusterLink[] = [...pairs.entries()]
    .sort(([x], [y]) => compareText(x, y))
    .map(([, entry]) => {
      const only = entry.styles.size === 1 ? [...entry.styles][0] : undefined;
      return { a: entry.a, b: entry.b, count: entry.count, style: only ?? "derived" };
    });

  const counts = new Map<EntityKind, number>();
  for (const entity of entities) counts.set(entity.kind, (counts.get(entity.kind) ?? 0) + 1);
  const kindOrder = Object.keys(tokens.kinds) as EntityKind[];
  const kindCounts = tokens.groupOrder.flatMap((group) =>
    kindOrder
      .filter((kind) => counts.has(kind) && kindVisual(kind).group === group)
      .map((kind) => ({ kind, count: counts.get(kind) ?? 0 })),
  );

  const complete = entities.length > 0 && entities.every((entity) => input.scores.has(entity.id));
  const importanceMethod = complete ? "pagerank" : "connections";
  const maxScore = complete ? Math.max(...entities.map((entity) => input.scores.get(entity.id) ?? 0)) : 0;
  const maxDegree = Math.max(1, ...entities.map((entity) => connections.get(entity.id)?.length ?? 0));
  const importance = (id: string): number => {
    if (complete && maxScore > 0) return Math.min(1, Math.max(0, (input.scores.get(id) ?? 0) / maxScore));
    return Math.min(1, (connections.get(id)?.length ?? 0) / maxDegree);
  };

  return {
    entities,
    byId,
    relationships,
    omittedRelationships: omitted,
    clusters,
    clusterOf,
    clusterLinks,
    connections,
    kindCounts,
    notice: input.notice,
    importanceMethod,
    importance,
  };
}
