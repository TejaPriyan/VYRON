// Runs the TypeScript client against the real Python API over a local socket.
// Not meant to be run by hand: tests/test_ts_integration.py starts the dev server and sets VYRON_API_URL.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { CommitActivity } from "../../types/src/api.ts";
import { buildOutline, describeSelection } from "../../graph/src/outline.ts";
import { ApiRequestError, VyronClient, walkPages } from "./client.ts";

const baseUrl = process.env["VYRON_API_URL"];
const skip = baseUrl ? false : "VYRON_API_URL is not set (the Python test starts the server)";
const client = new VyronClient({ baseUrl: baseUrl ?? "http://127.0.0.1:1", timeoutMs: 5_000 });

describe("TypeScript client against the Python API (seed data)", { skip }, () => {
  it("reads meta and says the data is development seed data", async () => {
    const meta = await client.get("get_meta");
    assert.equal(meta.name, "VYRON");
    assert.equal(meta.data_mode, "seed");
    assert.match(meta.data_notice ?? "", /seed data/i);
    assert.ok(meta.enums.entity_kinds.includes("language"));
    assert.equal(meta.enums.provenance.length, 7);
  });

  it("exposes rate-limit and request-id metadata", async () => {
    const result = await client.call("get_meta");
    assert.equal(result.kind, "ok");
    assert.ok(result.meta.requestId && result.meta.requestId.length > 0);
    assert.ok(result.meta.rateLimit && result.meta.rateLimit.limit >= result.meta.rateLimit.remaining);
  });

  it("revalidates with an ETag", async () => {
    const first = await client.call("get_meta");
    assert.ok(first.meta.etag);
    const second = await client.call("get_meta", { etag: first.meta.etag });
    assert.equal(second.kind, "not_modified");
    assert.equal(second.meta.status, 304);
  });

  it("walks every page of the entity list with signed cursors", async () => {
    const ids = new Set<string>();
    let pages = 0;
    for await (const page of walkPages(client, "list_entities", { query: { limit: 5 } })) {
      pages += 1;
      assert.ok(page.items.length <= 5);
      for (const entity of page.items) ids.add(entity.id);
    }
    assert.equal(ids.size, 60, "the seed holds 60 entities");
    assert.equal(pages, 12);
  });

  it("filters by kind", async () => {
    const page = await client.get("list_entities", { query: { kind: ["language"] } });
    assert.ok(page.items.length > 0);
    assert.ok(page.items.every((entity) => entity.kind === "language"));
    assert.ok(page.items.every((entity) => entity.provenance === "seed"));
  });

  it("finds entities by typo-tolerant search", async () => {
    const page = await client.get("search", { query: { q: "pytorch" } });
    assert.equal(page.items[0]?.slug, "pytorch");
  });

  it("resolves a slug, then loads the same entity by id and its neighbourhood", async () => {
    const bySlug = await client.get("get_entity_by_slug", { path: { kind: "language", slug: "python" } });
    const byId = await client.get("get_entity", { path: { entity_id: bySlug.id } });
    assert.deepEqual(byId, bySlug);
    const hood = await client.get("get_entity_neighbors", { path: { entity_id: bySlug.id }, query: { depth: 1 } });
    assert.equal(hood.center_id, bySlug.id);
    assert.ok(hood.entities.length > 1);
    assert.ok(hood.relationships.length >= 1);
    const known = new Set(hood.entities.map((entity) => entity.id));
    for (const edge of hood.relationships) {
      assert.ok(known.has(edge.source_id) && known.has(edge.target_id), "edges only connect returned entities");
    }
  });

  it("turns a real neighbourhood into an accessible outline with the seed notice", async () => {
    const python = await client.get("get_entity_by_slug", { path: { kind: "language", slug: "python" } });
    const hood = await client.get("get_entity_neighbors", { path: { entity_id: python.id }, query: { depth: 2 } });
    const outline = buildOutline(hood);
    assert.equal(outline.entityCount, hood.entities.length);
    assert.equal(outline.relationshipCount, hood.relationships.length);
    assert.equal(outline.omittedRelationships, 0);
    assert.match(outline.summary, /Development seed data/);
    const entry = outline.groups.flatMap((group) => group.entities).find((entity) => entity.id === python.id);
    assert.ok(entry && entry.relations.length >= 1);
    assert.ok(entry.relations.every((relation) => relation.sentence.startsWith("Python ") && relation.sentence.endsWith(".")));
    assert.match(describeSelection(entry), /^Python, language\. \d+ connections?\. Development seed\.$/);
    for (const group of outline.groups) {
      for (const member of group.entities) assert.equal(member.provenanceLabel, "Development seed");
    }
  });

  it("finds a path between connected entities and reports none across components", async () => {
    const python = await client.get("get_entity_by_slug", { path: { kind: "language", slug: "python" } });
    const nvidia = await client.get("get_entity_by_slug", { path: { kind: "company", slug: "nvidia" } });
    const apple = await client.get("get_entity_by_slug", { path: { kind: "company", slug: "apple" } });
    const connected = await client.get("graph_path", { query: { from: python.id, to: nvidia.id } });
    assert.equal(connected.found, true);
    assert.equal(connected.entity_ids[0], python.id);
    assert.equal(connected.entity_ids.at(-1), nvidia.id);
    assert.equal(connected.relationships.length, connected.entity_ids.length - 1);
    const apart = await client.get("graph_path", { query: { from: python.id, to: apple.id } });
    assert.equal(apart.found, false);
    assert.deepEqual(apart.entity_ids, []);
  });

  it("labels the overview ranking as an estimate", async () => {
    const overview = await client.get("graph_overview");
    assert.equal(overview.score_method, "pagerank");
    assert.equal(overview.score_provenance, "estimated");
    assert.equal(overview.entity_count, 60);
    assert.equal(overview.component_count, 4);
  });

  it("reports validation failures with field details", async () => {
    await assert.rejects(client.get("list_entities", { query: { limit: 1000 } }), (e: unknown) => {
      assert.ok(e instanceof ApiRequestError);
      assert.equal(e.status, 422);
      assert.equal(e.code, "validation_error");
      assert.ok(e.requestId);
      assert.ok(e.details.some((detail) => detail.field === "limit"));
      return true;
    });
  });

  it("reports an unknown entity as not_found", async () => {
    await assert.rejects(
      client.get("get_entity", { path: { entity_id: "00000000-0000-4000-8000-000000000000" } }),
      (e: unknown) => e instanceof ApiRequestError && e.status === 404 && e.code === "not_found",
    );
  });

  it("rejects a tampered cursor", async () => {
    await assert.rejects(
      client.get("list_entities", { query: { limit: 5, cursor: "not-a-real-cursor" } }),
      (e: unknown) => e instanceof ApiRequestError && e.status >= 400 && e.status < 500 && !e.retryable,
    );
  });

  it("answers planned routes with 501 rather than inventing data", async () => {
    await assert.rejects(client.get("get_me"), (e: unknown) => {
      assert.ok(e instanceof ApiRequestError);
      assert.equal(e.status, 501);
      assert.equal(e.code, "not_implemented");
      return true;
    });
  });

  it("parses a GitHub URL without calling GitHub", async () => {
    const target = await client.get("github_parse", { query: { input: "https://github.com/vercel/next.js/issues/42" } });
    assert.equal(target.kind, "issue");
    assert.equal(target.owner, "vercel");
    assert.equal(target.repo, "next.js");
    assert.equal(target.number, 42);
  });

  // The server under test is wired to a stand-in for api.github.com that serves FIXTURE data (scripts/standin_github.py),
  // so these show the typed client and the live-repository route agree, not what GitHub returns.
  it("reads a repository through the live route and gets a sourced answer with its own evidence", async () => {
    const analysis = await client.get("get_repository_analysis", { path: { owner: "fixture-org", repo: "example-repo" } });
    assert.equal(analysis.full_name, "fixture-org/example-repo");
    assert.equal(analysis.provenance, "sourced");
    assert.equal(analysis.cache_status, "fresh");
    assert.ok(analysis.source_urls.length >= 1 && analysis.source_urls.every((url) => url.startsWith("http://127.0.0.1")));
    assert.ok(Object.values(analysis.languages).reduce((sum, bytes) => sum + bytes, 0) > 0);
    assert.match(analysis.description ?? "", /not data from GitHub/);
    assert.ok(Date.parse(analysis.fetched_at) > 0);
  });

  it("reports a repository GitHub does not have as not_found", async () => {
    await assert.rejects(
      client.get("get_repository_analysis", { path: { owner: "fixture-org", repo: "missing-repo" } }),
      (e: unknown) => e instanceof ApiRequestError && e.status === 404 && e.code === "not_found",
    );
  });

  it("rejects names that are not repositories before anything is asked of GitHub", async () => {
    await assert.rejects(
      client.get("get_repository_analysis", { path: { owner: "-bad", repo: "repo" } }),
      (e: unknown) => e instanceof ApiRequestError && e.status === 422 && e.code === "validation_error",
    );
  });

  it("reads contributors and the latest release as a separate, sourced answer", async () => {
    const activity = await client.get("get_repository_activity", { path: { owner: "fixture-org", repo: "example-repo" } });
    assert.equal(activity.full_name, "fixture-org/example-repo");
    assert.equal(activity.provenance, "sourced");
    assert.equal(activity.contributors_status, "listed");
    assert.ok(activity.contributors.length > 0 && activity.contributors.length <= 10);
    assert.deepEqual(
      activity.contributors.map((c) => c.contributions),
      [...activity.contributors.map((c) => c.contributions)].sort((a, b) => b - a),
    );
    assert.equal(activity.latest_release?.tag_name, "v1.2.3");
    assert.equal(activity.source_urls.length, 3);
    assert.ok(activity.source_urls.every((url) => url.startsWith("http://127.0.0.1")));
  });

  it("answers a repository with nothing to list and no release with an empty list and a null release", async () => {
    const activity = await client.get("get_repository_activity", { path: { owner: "fixture-org", repo: "quiet-repo" } });
    assert.equal(activity.contributors_status, "none");
    assert.deepEqual(activity.contributors, []);
    assert.equal(activity.latest_release, null);
  });

  it("reports a refused contributor list as declined, not as an error", async () => {
    const activity = await client.get("get_repository_activity", { path: { owner: "fixture-org", repo: "huge-repo" } });
    assert.equal(activity.contributors_status, "declined");
    assert.ok(activity.latest_release !== null);
  });

  it("reports the activity of a repository GitHub does not have as not_found", async () => {
    await assert.rejects(
      client.get("get_repository_activity", { path: { owner: "fixture-org", repo: "missing-repo" } }),
      (e: unknown) => e instanceof ApiRequestError && e.status === 404 && e.code === "not_found",
    );
  });

  it("rejects names that are not repositories before the activity read asks GitHub anything", async () => {
    await assert.rejects(
      client.get("get_repository_activity", { path: { owner: "-bad", repo: "repo" } }),
      (e: unknown) => e instanceof ApiRequestError && e.status === 422 && e.code === "validation_error",
    );
  });

  // The stand-in answers 202 and then 200, as GitHub really did for commit activity on 2026-11-04 (the counts are made up).
  it("reads commit activity as 'computing' twice and then as a year of weeks, without the server ever waiting", async () => {
    const read = (): Promise<CommitActivity> => client.get("get_commit_activity", { path: { owner: "fixture-org", repo: "example-repo" } });
    const first = await read();
    assert.equal(first.status, "computing");
    assert.deepEqual(first.weeks, []);
    assert.equal(first.provenance, "sourced");
    assert.equal((await read()).status, "computing");
    const ready = await read();
    assert.equal(ready.status, "ready");
    assert.equal(ready.weeks.length, 52);
    assert.ok(ready.weeks.every((week) => week.days.length === 7 && week.total === week.days.reduce((a, b) => a + b, 0)));
    assert.equal(ready.source_urls.length, 2);
    assert.ok(ready.source_urls.every((url) => url.startsWith("http://127.0.0.1")));
  });

  it("reads commit activity that GitHub never finishes as 'computing' each time", async () => {
    for (let ask = 0; ask < 3; ask += 1) {
      const activity = await client.get("get_commit_activity", { path: { owner: "fixture-org", repo: "slow-stats-repo" } });
      assert.equal(activity.status, "computing");
    }
  });

  it("reports a repository with nothing to count as 'none', not as an empty chart", async () => {
    const activity = await client.get("get_commit_activity", { path: { owner: "fixture-org", repo: "quiet-repo" } });
    assert.deepEqual([activity.status, activity.weeks], ["none", []]);
  });

  it("reports the commit activity of a repository GitHub does not have as not_found", async () => {
    await assert.rejects(
      client.get("get_commit_activity", { path: { owner: "fixture-org", repo: "missing-repo" } }),
      (e: unknown) => e instanceof ApiRequestError && e.status === 404 && e.code === "not_found",
    );
  });

  it("rejects names that are not repositories before the commit activity read asks GitHub anything", async () => {
    await assert.rejects(
      client.get("get_commit_activity", { path: { owner: "-bad", repo: "repo" } }),
      (e: unknown) => e instanceof ApiRequestError && e.status === 422 && e.code === "validation_error",
    );
  });

  it("reads the quota the server's GitHub credentials have", async () => {
    const quota = await client.get("get_github_rate_limit");
    assert.equal(quota.resource, "core");
    assert.ok(quota.limit !== null && quota.remaining !== null && quota.remaining <= quota.limit);
  });
});
