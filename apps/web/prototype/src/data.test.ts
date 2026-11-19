import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { VyronClient } from "../../../../packages/api-client/src/client.ts";
import type { Entity, GraphOverview, Relationship } from "../../../../packages/types/src/api.ts";
import { loadAllEvents, loadAtlas, loadTimeline, MAX_NEIGHBOURHOOD_CALLS, MAX_TIMELINE_PAGES, searchEntities } from "./data.ts";

const id = (n: number): string => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const entity = (n: number): Entity => ({ id: id(n), kind: "technology", slug: `e${n}`, name: `Entity ${String(n).padStart(3, "0")}`, aliases: [], provenance: "seed" });
const rel = (a: number, b: number): Relationship => ({ source_id: id(a), target_id: id(b), type: "RELATED_TO", provenance: "seed", evidence_ids: [] });

interface Fake {
  readonly entities: Entity[];
  readonly relationships: Relationship[];
  /** Drops this many relationships from every neighbourhood answer, to simulate an incomplete response. */
  readonly dropFromNeighbourhoods?: number;
}

/** A tiny in-memory API: just enough of the contract for `loadAtlas`. */
function fakeApi(spec: Fake): { client: VyronClient; calls: string[]; signals: Array<AbortSignal | null | undefined> } {
  const calls: string[] = [];
  const signals: Array<AbortSignal | null | undefined> = [];
  const json = (body: unknown): Response => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  const adjacency = (start: string, depth: number): Set<string> => {
    const seen = new Set([start]);
    let frontier = [start];
    for (let d = 0; d < depth; d += 1) {
      const next: string[] = [];
      for (const r of spec.relationships) {
        for (const [a, b] of [[r.source_id, r.target_id], [r.target_id, r.source_id]] as const) {
          if (frontier.includes(a) && !seen.has(b)) {
            seen.add(b);
            next.push(b);
          }
        }
      }
      frontier = next;
    }
    return seen;
  };
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = new URL(String(input), "http://fake.invalid");
    calls.push(url.pathname);
    signals.push(init?.signal);
    if (url.pathname === "/api/v1/graph/overview") {
      const overview: GraphOverview = {
        entity_count: spec.entities.length,
        relationship_count: spec.relationships.length,
        component_count: 1,
        top_entities: spec.entities.slice(0, 20).map((e, i) => ({ entity: e, score: 1 / (i + 2) })),
        clusters: [{ id: id(900), label: "All", size: spec.entities.length, member_ids: spec.entities.slice(0, 300).map((e) => e.id) }],
        score_method: "pagerank",
        score_provenance: "estimated",
        data_notice: "Development seed data.",
      };
      return json(overview);
    }
    if (url.pathname === "/api/v1/entities") {
      const limit = Number(url.searchParams.get("limit") ?? 25);
      const offset = Number(url.searchParams.get("cursor") ?? 0);
      const items = spec.entities.slice(offset, offset + limit);
      const nextOffset = offset + limit;
      return json({ items, page: { next_cursor: nextOffset < spec.entities.length ? String(nextOffset) : null, total: spec.entities.length } });
    }
    const neighbours = /^\/api\/v1\/entities\/([^/]+)\/neighbors$/.exec(url.pathname);
    if (neighbours) {
      const reached = adjacency(neighbours[1] as string, Number(url.searchParams.get("depth") ?? 1));
      const relationships = spec.relationships.filter((r) => reached.has(r.source_id) && reached.has(r.target_id)).slice(spec.dropFromNeighbourhoods ?? 0);
      return json({ center_id: neighbours[1], depth: 3, entities: spec.entities.filter((e) => reached.has(e.id)), relationships, truncated: false, data_notice: null });
    }
    return new Response(JSON.stringify({ error: { code: "not_found", message: "no", request_id: "r" } }), { status: 404, headers: { "content-type": "application/json" } });
  };
  return { client: new VyronClient({ baseUrl: "", fetch: fetchImpl }), calls, signals };
}

describe("loadAtlas", () => {
  it("collects every entity and relationship, one connected component per request", async () => {
    const entities = [1, 2, 3, 4, 5, 6, 7].map(entity);
    const relationships = [rel(1, 2), rel(2, 3), rel(4, 5), rel(6, 7)];
    const { client, calls } = fakeApi({ entities, relationships });
    const load = await loadAtlas(client);
    assert.equal(load.atlas.entities.length, 7);
    assert.equal(load.atlas.relationships.length, 4);
    assert.deepEqual(load.completeness, { entities: { loaded: 7, expected: 7 }, relationships: { loaded: 4, expected: 4 }, complete: true });
    assert.equal(calls.filter((c) => c.endsWith("/neighbors")).length, 3, "three components, three neighbourhoods");
    assert.equal(load.atlas.notice, "Development seed data.");
    assert.equal(load.atlas.clusters.length, 1);
  });

  it("walks every page of the entity list", async () => {
    const entities = Array.from({ length: 230 }, (_, i) => entity(i + 1));
    const { client, calls } = fakeApi({ entities, relationships: [] });
    const load = await loadAtlas(client, undefined);
    assert.equal(load.atlas.entities.length, 230);
    assert.equal(calls.filter((c) => c === "/api/v1/entities").length, 3);
  });

  it("says so when it could not collect everything, and stops at the request ceiling", async () => {
    const entities = Array.from({ length: 60 }, (_, i) => entity(i + 1));
    const { client, calls } = fakeApi({ entities, relationships: [] }); // 60 isolated entities: 60 components
    const load = await loadAtlas(client);
    assert.equal(calls.filter((c) => c.endsWith("/neighbors")).length, MAX_NEIGHBOURHOOD_CALLS);
    assert.equal(load.atlas.entities.length, 60, "entities still all load");
    assert.equal(load.completeness.complete, true, "isolated entities have no relationships to miss");
    const linked = Array.from({ length: 60 }, (_, i) => entity(i + 1));
    const pairs = Array.from({ length: 30 }, (_, i) => rel(2 * i + 1, 2 * i + 2)); // 30 components of two
    const capped = await loadAtlas(fakeApi({ entities: linked, relationships: pairs }).client);
    assert.equal(capped.completeness.complete, false);
    assert.equal(capped.completeness.relationships.expected, 30);
    assert.equal(capped.completeness.relationships.loaded, MAX_NEIGHBOURHOOD_CALLS);
  });

  it("reports an incomplete relationship set when the API's own count is higher", async () => {
    const entities = [1, 2, 3].map(entity);
    const { client } = fakeApi({ entities, relationships: [rel(1, 2), rel(2, 3)], dropFromNeighbourhoods: 1 });
    const load = await loadAtlas(client);
    assert.equal(load.completeness.complete, false);
    assert.equal(load.completeness.relationships.expected, 2);
    assert.equal(load.completeness.relationships.loaded, 1);
  });

  it("passes the abort signal to every request", async () => {
    const { client, signals } = fakeApi({ entities: [1, 2].map(entity), relationships: [rel(1, 2)] });
    const controller = new AbortController();
    await loadAtlas(client, controller.signal);
    assert.ok(signals.length >= 3);
    assert.ok(signals.every((signal) => signal instanceof AbortSignal), "each request carries a signal");
  });

  it("fails with the client's error when the API does", async () => {
    const client = new VyronClient({ baseUrl: "", fetch: async () => new Response("down", { status: 503 }) });
    await assert.rejects(() => loadAtlas(client), { name: "ApiRequestError" });
  });
});

describe("loadTimeline and searchEntities", () => {
  it("asks for at most eight search results and returns them", async () => {
    let seen = "";
    const client = new VyronClient({
      baseUrl: "",
      fetch: async (input) => {
        seen = String(input);
        return new Response(JSON.stringify({ items: [entity(1)], page: { next_cursor: null, total: 1 } }), { status: 200, headers: { "content-type": "application/json" } });
      },
    });
    const found = await searchEntities(client, "type script");
    assert.equal(found.length, 1);
    assert.match(seen, /\/api\/v1\/search\?/);
    assert.match(seen, /q=type\+script|q=type%20script/);
    assert.match(seen, /limit=8/);
  });

  it("collects a timeline", async () => {
    const event = { id: id(5), entity_id: id(1), occurred_on: "1991-01-01", date_precision: "year", title: "Python first released", event_type: "release", provenance: "seed", is_prediction: false, evidence_ids: [] };
    const client = new VyronClient({
      baseUrl: "",
      fetch: async () => new Response(JSON.stringify({ items: [event], page: { next_cursor: null, total: 1 } }), { status: 200, headers: { "content-type": "application/json" } }),
    });
    const events = await loadTimeline(client, id(1));
    assert.deepEqual(events.map((e) => e.title), ["Python first released"]);
  });
});

describe("loadAllEvents", () => {
  const dated = (n: number) => ({ id: id(1000 + n), entity_id: id(n), occurred_on: `${1970 + n}-01-01`, date_precision: "year", title: `Event ${n}`, event_type: "release", provenance: "seed", is_prediction: false, description: null });
  const body = (items: unknown[], total: number, next: string | null, notice: string | null = "Development seed data.") => ({ items, page: { limit: 100, total, next_cursor: next }, include_predictions: false, data_notice: notice });
  const answer = (value: unknown): Response => new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } });

  it("follows the cursor, asks for 100 at a time and reports a complete walk", async () => {
    const urls: string[] = [];
    const client = new VyronClient({
      baseUrl: "",
      fetch: async (input) => {
        const url = String(input);
        urls.push(url);
        return url.includes("cursor=two") ? answer(body([dated(3)], 3, null)) : answer(body([dated(1), dated(2)], 3, "two"));
      },
    });
    const loaded = await loadAllEvents(client);
    assert.deepEqual(loaded.events.map((e) => e.title), ["Event 1", "Event 2", "Event 3"]);
    assert.equal(loaded.expected, 3);
    assert.equal(loaded.complete, true);
    assert.equal(loaded.notice, "Development seed data.");
    assert.match(urls[0] ?? "", /\/api\/v1\/timeline\?/);
    assert.match(urls[0] ?? "", /limit=100/);
    assert.doesNotMatch(urls[0] ?? "", /include_predictions/);
  });

  it("says so when it stopped before the API ran out, rather than drawing half an axis as if it were whole", async () => {
    let page = 0;
    const client = new VyronClient({
      baseUrl: "",
      fetch: async () => {
        page += 1;
        return answer(body([dated(page)], 999, `cursor-${page}`));
      },
    });
    const loaded = await loadAllEvents(client);
    assert.equal(page, MAX_TIMELINE_PAGES);
    assert.equal(loaded.events.length, MAX_TIMELINE_PAGES);
    assert.equal(loaded.expected, 999);
    assert.equal(loaded.complete, false);
  });

  it("returns an empty, complete result for an empty timeline and passes the abort signal on", async () => {
    const signals: Array<AbortSignal | null | undefined> = [];
    const client = new VyronClient({
      baseUrl: "",
      fetch: async (_input, init) => {
        signals.push(init?.signal);
        return answer(body([], 0, null, null));
      },
    });
    const controller = new AbortController();
    const loaded = await loadAllEvents(client, controller.signal);
    assert.deepEqual(loaded, { events: [], expected: 0, complete: true, notice: null });
    assert.ok(signals.every((signal) => signal instanceof AbortSignal));
  });

  it("fails with the client's error when the API does", async () => {
    const client = new VyronClient({ baseUrl: "", fetch: async () => new Response("down", { status: 503 }) });
    await assert.rejects(() => loadAllEvents(client), { name: "ApiRequestError" });
  });
});
