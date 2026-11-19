// Run: node --test packages/graph/src/outline.test.ts
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { RELATION_TYPE_VALUES } from "../../types/src/api.ts";
import type { Entity, Relationship } from "../../types/src/api.ts";
import { tokens } from "../../ui/src/tokens.ts";
import { RELATION_PHRASES, buildOutline, describeSelection } from "./outline.ts";

const entity = (id: string, name: string, kind: Entity["kind"], provenance: Entity["provenance"] = "seed"): Entity => ({
  id,
  kind,
  slug: name.toLowerCase(),
  name,
  aliases: [],
  provenance,
});
const edge = (source_id: string, target_id: string, type: Relationship["type"], provenance: Relationship["provenance"] = "seed"): Relationship => ({
  source_id,
  target_id,
  type,
  provenance,
} as Relationship);

const python = entity("p", "Python", "language");
const numpy = entity("n", "NumPy", "library");
const nvidia = entity("v", "NVIDIA", "company", "sourced");
const graph = {
  entities: [nvidia, numpy, python],
  relationships: [edge("n", "p", "USES"), edge("v", "n", "RELATED_TO", "inferred")],
  truncated: false,
  data_notice: null,
};

describe("RELATION_PHRASES", () => {
  it("has words for every relation type in the contract, in both directions", () => {
    assert.deepEqual(Object.keys(RELATION_PHRASES).sort(), [...RELATION_TYPE_VALUES].sort());
    for (const phrase of Object.values(RELATION_PHRASES)) {
      assert.ok(phrase.out.length > 0 && phrase.in.length > 0);
      assert.doesNotMatch(phrase.out + phrase.in, /[A-Z_]/, "phrases are plain lower-case words");
    }
  });
});

describe("buildOutline", () => {
  it("writes each relationship as a sentence from both ends", () => {
    const outline = buildOutline(graph);
    const flat = outline.groups.flatMap((g) => g.entities);
    const byName = Object.fromEntries(flat.map((e) => [e.name, e]));
    assert.deepEqual(byName["NumPy"]!.relations.map((r) => r.sentence), ["NumPy is related to NVIDIA.", "NumPy uses Python."]);
    assert.deepEqual(byName["Python"]!.relations.map((r) => r.sentence), ["Python is used by NumPy."]);
    assert.deepEqual(byName["NVIDIA"]!.relations.map((r) => r.sentence), ["NVIDIA is related to NumPy."]);
    assert.equal(outline.relationshipCount, 2);
  });

  it("orders groups as the legend does, omits empty groups, and sorts names without regard to case", () => {
    const outline = buildOutline({ ...graph, entities: [...graph.entities, entity("a", "alpha", "library"), entity("z", "Zed", "library")] });
    assert.deepEqual(outline.groups.map((g) => g.group), ["software", "organizations"]);
    assert.deepEqual(outline.groups[0]!.entities.map((e) => e.name), ["alpha", "NumPy", "Python", "Zed"]);
    assert.equal(outline.groups[0]!.label, tokens.groups.software.label);
  });

  it("labels kind and provenance in words, never by colour", () => {
    const outline = buildOutline(graph);
    const flat = outline.groups.flatMap((g) => g.entities);
    assert.equal(flat.find((e) => e.name === "Python")!.kindLabel, "Language");
    assert.equal(flat.find((e) => e.name === "Python")!.provenanceLabel, "Development seed");
    assert.equal(flat.find((e) => e.name === "NVIDIA")!.provenanceLabel, "Sourced");
    assert.equal(flat.find((e) => e.name === "NumPy")!.relations.find((r) => r.otherName === "NVIDIA")!.provenanceLabel, "Inferred");
  });

  it("counts relationships whose other end was cut off instead of inventing it", () => {
    const outline = buildOutline({ ...graph, relationships: [...graph.relationships, edge("n", "missing", "DEPENDS_ON")], truncated: true });
    assert.equal(outline.omittedRelationships, 1);
    assert.equal(outline.relationshipCount, 2);
    assert.ok(!JSON.stringify(outline).includes("missing"));
    assert.match(outline.summary, /limited; some connections are not shown/);
  });

  it("summarises counts with correct plurals and appends the seed notice", () => {
    const one = buildOutline({ entities: [python], relationships: [], truncated: false, data_notice: null });
    assert.equal(one.summary, "1 entity in 1 group, 0 relationships.");
    const many = buildOutline({ ...graph, data_notice: "Development seed data. Not verified production facts." });
    assert.equal(many.summary, "3 entities in 2 groups, 2 relationships. Development seed data. Not verified production facts.");
  });

  it("is independent of input order", () => {
    const shuffled = { ...graph, entities: [...graph.entities].reverse(), relationships: [...graph.relationships].reverse() };
    assert.deepEqual(buildOutline(shuffled), buildOutline(graph));
  });

  it("handles an empty graph", () => {
    const outline = buildOutline({ entities: [], relationships: [], truncated: false, data_notice: null });
    assert.deepEqual(outline.groups, []);
    assert.equal(outline.summary, "0 entities in 0 groups, 0 relationships.");
  });

  it("announces a selection with name, kind, connection count and provenance", () => {
    const outline = buildOutline(graph);
    const numpyEntry = outline.groups.flatMap((g) => g.entities).find((e) => e.name === "NumPy")!;
    assert.equal(describeSelection(numpyEntry), "NumPy, library. 2 connections. Development seed.");
    const lone = buildOutline({ entities: [python], relationships: [], truncated: false, data_notice: null }).groups[0]!.entities[0]!;
    assert.equal(describeSelection(lone), "Python, language. 1 connection. Development seed.".replace("1 connection", "0 connections"));
  });
});
