// Runs the prototype's data loading against the real Python API over a local socket.
// Not meant to be run by hand: tests/test_ts_integration.py starts the dev server and sets VYRON_API_URL.
import assert from "node:assert/strict";
import { before, describe, it } from "node:test";

import { VyronClient } from "../../../../packages/api-client/src/client.ts";
import { UNCLUSTERED_ID } from "../../../../packages/graph/src/atlas.ts";
import { computeLayout } from "../../../../packages/graph/src/layout.ts";
import { loadAtlas, loadTimeline, searchEntities } from "./data.ts";
import type { AtlasLoad } from "./data.ts";

const baseUrl = process.env["VYRON_API_URL"];
const skip = baseUrl ? false : "VYRON_API_URL is not set (the Python test starts the server)";
const client = new VyronClient({ baseUrl: baseUrl ?? "http://127.0.0.1:1", timeoutMs: 5_000 });

describe("the map's data from the real API (development seed)", { skip }, () => {
  let loaded: AtlasLoad;

  before(async () => {
    if (!skip) loaded = await loadAtlas(client);
  });

  it("loads every entity and relationship the API reports, and says so", () => {
    const { atlas, overview, completeness, requests } = loaded;
    assert.equal(atlas.entities.length, 60);
    assert.equal(atlas.relationships.length, 68);
    assert.equal(atlas.omittedRelationships, 0);
    assert.equal(overview.entity_count, 60);
    assert.equal(overview.relationship_count, 68);
    assert.equal(completeness.complete, true);
    assert.ok(requests <= 30, `loading should stay far below the 60 a minute graph limit (${requests} calls)`);
  });

  it("names the data as development seed data", () => {
    const { atlas } = loaded;
    assert.match(atlas.notice ?? "", /seed data/i);
    assert.ok(atlas.entities.every((entity) => entity.provenance === "seed"));
  });

  it("places every entity in a cluster the API detected, with no catch-all needed", () => {
    const { atlas } = loaded;
    assert.equal(atlas.clusters.length, 20);
    assert.ok(atlas.clusters.every((cluster) => cluster.detected));
    assert.ok(!atlas.clusters.some((cluster) => cluster.id === UNCLUSTERED_ID));
    for (const entity of atlas.entities) assert.ok(atlas.clusterOf.has(entity.id), `${entity.name} has no cluster`);
    const sizes = atlas.clusters.map((cluster) => cluster.size).sort((a, b) => b - a);
    assert.deepEqual(sizes, [6, 5, 4, 4, 4, 3, 3, 3, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2]);
  });

  it("scores importance from PageRank or connections for every entity", () => {
    const { atlas } = loaded;
    assert.equal(atlas.importanceMethod, "connections");
    const top = atlas.entities.map((entity) => atlas.importance(entity.id));
    assert.equal(Math.max(...top), 1);
    assert.ok(top.every((value) => value >= 0 && value <= 1));
  });

  it("lays out the real graph deterministically with the clusters apart", () => {
    const { atlas } = loaded;
    const nodes = atlas.entities.map((entity) => ({ id: entity.id, group: atlas.clusterOf.get(entity.id) ?? UNCLUSTERED_ID }));
    const links = atlas.relationships.map((r) => ({ source: r.source_id, target: r.target_id }));
    const first = computeLayout(nodes, links);
    const second = computeLayout(nodes, links);
    assert.deepEqual([...first.positions], [...second.positions]);
    assert.equal(first.positions.size, 60);
  });

  it("finds an entity by name and reads its dated events", async () => {
    const hits = await searchEntities(client, "post");
    assert.deepEqual(hits.map((entity) => entity.name), ["PostgreSQL"]);
    const python = (await searchEntities(client, "python")).find((entity) => entity.name === "Python");
    assert.ok(python);
    const events = await loadTimeline(client, python.id);
    assert.ok(events.length >= 1);
    assert.ok(events.every((event) => event.provenance === "seed"));
  });

  it("returns nothing for a search that matches nothing", async () => {
    assert.deepEqual(await searchEntities(client, "zzzzqqqq"), []);
  });
});
