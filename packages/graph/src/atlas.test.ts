import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { Entity, EntityKind, GraphOverview, Provenance, Relationship, RelationType } from "../../types/src/api.ts";
import { buildAtlas, UNCLUSTERED_ID } from "./atlas.ts";

const id = (n: number): string => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const entity = (n: number, name: string, kind: EntityKind = "technology"): Entity => ({
  id: id(n), kind, slug: name.toLowerCase(), name, aliases: [], provenance: "seed",
});
const rel = (a: number, type: RelationType, b: number, provenance: Provenance = "seed"): Relationship => ({
  source_id: id(a), target_id: id(b), type, provenance, evidence_ids: [],
});
const cluster = (n: number, label: string, members: number[], size = members.length): GraphOverview["clusters"][number] => ({
  id: id(n), label, size, member_ids: members.map(id),
});

const entities = [
  entity(1, "Python", "language"), entity(2, "NumPy", "library"), entity(3, "PyTorch", "framework"),
  entity(4, "Docker"), entity(5, "Kubernetes"), entity(6, "NVIDIA", "company"),
];
const relationships = [rel(2, "BUILT_ON", 1), rel(3, "USES", 1), rel(4, "RELATED_TO", 5), rel(3, "USES", 6)];

describe("buildAtlas", () => {
  const atlas = buildAtlas({
    entities, relationships, notice: "Development seed data.",
    clusters: [cluster(100, "Python", [1, 2, 3]), cluster(101, "Docker", [4, 5])],
    scores: new Map(entities.map((e, i) => [e.id, (6 - i) / 21])),
  });

  it("sorts entities by name and indexes them by id", () => {
    assert.deepEqual(atlas.entities.map((e) => e.name), ["Docker", "Kubernetes", "NumPy", "NVIDIA", "Python", "PyTorch"]);
    assert.equal(atlas.byId.get(id(3))?.name, "PyTorch");
    assert.equal(atlas.notice, "Development seed data.");
  });

  it("assigns every entity to a cluster, putting unplaced ones in a catch-all group", () => {
    assert.equal(atlas.clusterOf.get(id(1)), id(100));
    assert.equal(atlas.clusterOf.get(id(5)), id(101));
    assert.equal(atlas.clusterOf.get(id(6)), UNCLUSTERED_ID);
    const other = atlas.clusters.find((c) => c.id === UNCLUSTERED_ID);
    assert.deepEqual(other, { id: UNCLUSTERED_ID, label: "Other", size: 1, memberIds: [id(6)], detected: false });
    assert.equal(atlas.clusters.filter((c) => c.detected).length, 2);
    assert.equal(atlas.clusterOf.size, atlas.entities.length);
  });

  it("lists connections both ways, sorted by the other entity's name", () => {
    const python = atlas.connections.get(id(1)) ?? [];
    assert.deepEqual(python.map((c) => [c.otherId, c.direction]), [[id(2), "in"], [id(3), "in"]]);
    const torch = atlas.connections.get(id(3)) ?? [];
    assert.deepEqual(torch.map((c) => [c.otherId, c.direction]), [[id(6), "out"], [id(1), "out"]]);
    assert.equal(atlas.connections.get(id(6))?.[0]?.direction, "in");
  });

  it("counts relationships between clusters, and only between clusters", () => {
    assert.deepEqual(atlas.clusterLinks.map((l) => [l.count, l.style]), [[1, "working"]]);
    const link = atlas.clusterLinks[0];
    assert.deepEqual(new Set([link?.a, link?.b]), new Set([id(100), UNCLUSTERED_ID]));
  });

  it("counts kinds in the order of the legend: group first, then kind", () => {
    assert.deepEqual(atlas.kindCounts, [
      { kind: "technology", count: 2 }, { kind: "language", count: 1 }, { kind: "framework", count: 1 },
      { kind: "library", count: 1 }, { kind: "company", count: 1 },
    ]);
  });

  it("scales importance to the highest score when every entity has one", () => {
    assert.equal(atlas.importanceMethod, "pagerank");
    assert.equal(atlas.importance(id(1)), 1);
    assert.ok(atlas.importance(id(6)) < atlas.importance(id(2)));
    assert.equal(atlas.importance("unknown"), 0);
  });

  it("falls back to connection count when scores are missing", () => {
    const partial = buildAtlas({ entities, relationships, clusters: [], scores: new Map([[id(1), 0.9]]), notice: null });
    assert.equal(partial.importanceMethod, "connections");
    assert.equal(partial.importance(id(1)), 1, "Python and PyTorch both have two connections: the maximum");
    assert.equal(partial.importance(id(3)), 1);
    assert.equal(partial.importance(id(4)), 0.5);
  });

  it("does not drop or invent anything quietly", () => {
    const messy = buildAtlas({
      entities: [...entities, entities[0] as Entity],
      relationships: [...relationships, relationships[0] as Relationship, rel(1, "USES", 999)],
      clusters: [cluster(100, "Python", [1, 2, 3, 777]), cluster(102, "Empty", [888])],
      scores: new Map(),
      notice: null,
    });
    assert.equal(messy.entities.length, 6, "a repeated entity is counted once");
    assert.equal(messy.relationships.length, 4, "a repeated relationship is counted once");
    assert.equal(messy.omittedRelationships, 1, "a relationship to an unknown entity is reported, not drawn");
    assert.ok(!messy.clusters.some((c) => c.id === id(102)), "a cluster with no loaded members is not drawn");
    assert.deepEqual(messy.clusters.find((c) => c.id === id(100))?.memberIds, [id(1), id(2), id(3)]);
  });

  it("keeps a cluster's real size when only some members are loaded", () => {
    const cut = buildAtlas({ entities, relationships, clusters: [cluster(100, "Python", [1, 2], 40)], scores: new Map(), notice: null });
    assert.equal(cut.clusters[0]?.size, 40);
    assert.equal(cut.clusters[0]?.memberIds.length, 2);
  });

  it("marks a link between clusters as derived when the relationships under it differ in provenance", () => {
    const mixed = buildAtlas({
      entities,
      relationships: [rel(1, "USES", 4, "seed"), rel(2, "USES", 5, "sourced")],
      clusters: [cluster(100, "A", [1, 2]), cluster(101, "B", [4, 5])],
      scores: new Map(),
      notice: null,
    });
    assert.deepEqual(mixed.clusterLinks.map((l) => [l.count, l.style]), [[2, "derived"]]);
  });

  it("handles an empty graph", () => {
    const empty = buildAtlas({ entities: [], relationships: [], clusters: [], scores: new Map(), notice: null });
    assert.equal(empty.entities.length, 0);
    assert.equal(empty.clusters.length, 0);
    assert.equal(empty.importance("x"), 0);
  });
});
