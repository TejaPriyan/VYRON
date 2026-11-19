// Run: node --test packages/api-client/src/client.test.ts   (Node >= 22.18, no flags, no packages)
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ApiRequestError, VyronClient, encodeQuery, expandPath, walkPages } from "./client.ts";

type Recorded = { url: string; init: RequestInit };

function stub(
  responder: (url: string, init: RequestInit) => Response | Promise<Response>,
): { fetch: typeof globalThis.fetch; calls: Recorded[] } {
  const calls: Recorded[] = [];
  const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init: init ?? {} });
    return responder(url, init ?? {});
  }) as typeof globalThis.fetch;
  return { fetch, calls };
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}): Response =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

const envelope = (code: string, message: string, extra: Record<string, unknown> = {}) => ({
  error: { code, message, request_id: "req-1", ...extra },
});

describe("expandPath", () => {
  it("substitutes and percent-encodes values", () => {
    assert.equal(expandPath("/a/{entity_id}/b", { entity_id: "x y/z" }), "/a/x%20y%2Fz/b");
  });
  it("rejects missing, empty and unexpected parameters", () => {
    assert.throws(() => expandPath("/a/{entity_id}", {}), /missing path parameter "entity_id"/);
    assert.throws(() => expandPath("/a/{entity_id}", { entity_id: "" }), /missing path parameter/);
    assert.throws(() => expandPath("/a", { extra: "1" }), /unexpected path parameter "extra"/);
  });
  it("leaves a path without parameters alone", () => {
    assert.equal(expandPath("/api/v1/meta", undefined), "/api/v1/meta");
  });
});

describe("encodeQuery", () => {
  it("repeats arrays, skips undefined, stringifies scalars", () => {
    assert.equal(encodeQuery({ kind: ["language", "framework"], limit: 5, flag: true, none: undefined }), "?kind=language&kind=framework&limit=5&flag=true");
  });
  it("returns an empty string when nothing is set", () => {
    assert.equal(encodeQuery({}), "");
    assert.equal(encodeQuery(undefined), "");
    assert.equal(encodeQuery({ kind: [] }), "");
  });
  it("encodes reserved characters", () => {
    assert.equal(encodeQuery({ q: "c++ & rust" }), "?q=c%2B%2B+%26+rust");
  });
});

describe("VyronClient construction", () => {
  it("accepts http(s) URLs and path prefixes", () => {
    for (const ok of ["http://127.0.0.1:8000", "https://api.example.com", "https://api.example.com/base/", "", "/bff"]) {
      assert.doesNotThrow(() => new VyronClient({ baseUrl: ok, fetch: stub(() => json({})).fetch }), ok);
    }
  });
  it("rejects other schemes and malformed values", () => {
    for (const bad of ["javascript:alert(1)", "ftp://example.com", "http://", "example.com", "http://a b", "//evil.example", "/\\evil.example", "https://host\\evil", "https://h?x=1", "https://h#frag", "/bff?x=1"]) {
      assert.throws(() => new VyronClient({ baseUrl: bad }), TypeError, bad);
    }
  });
  it("builds URLs from the route table", () => {
    const client = new VyronClient({ baseUrl: "http://h:1/", fetch: stub(() => json({})).fetch });
    assert.equal(client.url("get_meta"), "http://h:1/api/v1/meta");
    assert.equal(
      client.url("list_entities", { query: { kind: ["language", "framework"], limit: 5 } }),
      "http://h:1/api/v1/entities?kind=language&kind=framework&limit=5",
    );
    assert.equal(client.url("get_entity", { path: { entity_id: "abc" } }), "http://h:1/api/v1/entities/abc");
    assert.throws(() => client.url("get_entity"), /missing path parameter/);
  });
});

describe("VyronClient.call", () => {
  it("returns data and response metadata", async () => {
    const { fetch, calls } = stub(() =>
      json({ ok: true }, 200, { "X-Request-ID": "r-9", ETag: '"abc"', "RateLimit-Limit": "60", "RateLimit-Remaining": "59", "RateLimit-Reset": "30" }),
    );
    const client = new VyronClient({ baseUrl: "http://h", fetch });
    const result = await client.call("get_meta");
    assert.equal(result.kind, "ok");
    assert.deepEqual(result.kind === "ok" ? result.data : null, { ok: true });
    assert.deepEqual(result.meta, { status: 200, requestId: "r-9", etag: '"abc"', rateLimit: { limit: 60, remaining: 59, resetSeconds: 30 } });
    const sent = calls[0]!;
    assert.equal(sent.init.method, "GET");
    assert.equal((sent.init.headers as Record<string, string>)["accept"], "application/json");
    assert.equal(sent.init.body, undefined);
    assert.equal((sent.init.headers as Record<string, string>)["content-type"], undefined);
  });

  it("omits rate-limit info unless all three headers parse", async () => {
    const { fetch } = stub(() => json({}, 200, { "RateLimit-Limit": "60", "RateLimit-Remaining": "x", "RateLimit-Reset": "3" }));
    const result = await new VyronClient({ baseUrl: "http://h", fetch }).call("get_meta");
    assert.equal(result.meta.rateLimit, null);
  });

  it("sends If-None-Match and reports 304 as not_modified", async () => {
    const { fetch, calls } = stub(() => new Response(null, { status: 304, headers: { ETag: '"abc"' } }));
    const result = await new VyronClient({ baseUrl: "http://h", fetch }).call("get_meta", { etag: '"abc"' });
    assert.equal(result.kind, "not_modified");
    assert.equal((calls[0]!.init.headers as Record<string, string>)["if-none-match"], '"abc"');
  });

  it("get() refuses a 304 it did not ask for", async () => {
    const { fetch } = stub(() => new Response(null, { status: 304 }));
    await assert.rejects(new VyronClient({ baseUrl: "http://h", fetch }).get("get_meta"), (e: unknown) => e instanceof ApiRequestError && e.code === "invalid_response");
  });

  it("turns the error envelope into ApiRequestError", async () => {
    const body = envelope("validation_error", "Bad input.", { details: [{ field: "limit", issue: "must be at most 100" }] });
    const { fetch } = stub(() => json(body, 422, { "Retry-After": "7" }));
    await assert.rejects(new VyronClient({ baseUrl: "http://h", fetch }).get("list_entities"), (e: unknown) => {
      assert.ok(e instanceof ApiRequestError);
      assert.equal(e.status, 422);
      assert.equal(e.code, "validation_error");
      assert.equal(e.requestId, "req-1");
      assert.deepEqual(e.details, [{ field: "limit", issue: "must be at most 100" }]);
      assert.equal(e.retryAfterSeconds, 7);
      assert.equal(e.retryable, false);
      return true;
    });
  });

  it("marks rate limiting and upstream failures as retryable", async () => {
    for (const [code, status] of [["rate_limited", 429], ["service_unavailable", 503], ["upstream_error", 502], ["upstream_rate_limited", 503]] as const) {
      const { fetch } = stub(() => json(envelope(code, "later"), status, { "Retry-After": "2" }));
      await assert.rejects(new VyronClient({ baseUrl: "http://h", fetch }).get("get_meta"), (e: unknown) => e instanceof ApiRequestError && e.retryable && e.retryAfterSeconds === 2);
    }
  });

  it("does not treat an error without the standard body as understood", async () => {
    const { fetch } = stub(() => new Response("<html>Bad gateway</html>", { status: 502, headers: { "content-type": "text/html" } }));
    await assert.rejects(new VyronClient({ baseUrl: "http://h", fetch }).get("get_meta"), (e: unknown) => {
      assert.ok(e instanceof ApiRequestError);
      assert.equal(e.code, "invalid_response");
      assert.equal(e.status, 502);
      assert.doesNotMatch(e.message, /Bad gateway/, "must not echo the upstream body");
      return true;
    });
  });

  it("rejects a 200 whose body is not JSON, or empty", async () => {
    for (const body of ["not json", ""]) {
      const { fetch } = stub(() => new Response(body, { status: 200 }));
      await assert.rejects(new VyronClient({ baseUrl: "http://h", fetch }).get("get_meta"), (e: unknown) => e instanceof ApiRequestError && e.code === "invalid_response");
    }
  });

  it("reports network failures, timeouts and caller cancellation distinctly", async () => {
    const down = stub(() => {
      throw new TypeError("fetch failed");
    });
    await assert.rejects(new VyronClient({ baseUrl: "http://h", fetch: down.fetch }).get("get_meta"), (e: unknown) => e instanceof ApiRequestError && e.code === "network_error" && e.retryable);

    const keepAlive = setTimeout(() => undefined, 2_000); // AbortSignal.timeout's timer is unref'd; a real socket would hold the loop
    const hang = stub((_url, init) => new Promise<Response>((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))));
    await assert.rejects(new VyronClient({ baseUrl: "http://h", fetch: hang.fetch, timeoutMs: 20 }).get("get_meta"), (e: unknown) => e instanceof ApiRequestError && e.code === "timeout");

    const controller = new AbortController();
    const pending = new VyronClient({ baseUrl: "http://h", fetch: hang.fetch }).get("get_meta", { signal: controller.signal });
    controller.abort();
    await assert.rejects(pending, (e: unknown) => e instanceof ApiRequestError && e.code === "aborted" && !e.retryable);
    clearTimeout(keepAlive);
  });

  it("sends a JSON body with a content type for POST", async () => {
    const { fetch, calls } = stub(() => json({ id: "r" }, 202));
    const client = new VyronClient({ baseUrl: "http://h", fetch });
    await client.get("create_research_run", { body: { kind: "research", question: "What is Rust?" } as never });
    const sent = calls[0]!;
    assert.equal(sent.init.method, "POST");
    assert.equal((sent.init.headers as Record<string, string>)["content-type"], "application/json");
    assert.equal(sent.init.body, JSON.stringify({ kind: "research", question: "What is Rust?" }));
  });

  it("adds fixed headers and the credentials mode to every request", async () => {
    const { fetch, calls } = stub(() => json({}));
    const client = new VyronClient({ baseUrl: "http://h", fetch, headers: { "x-test": "1" }, credentials: "include" });
    await client.get("get_meta");
    assert.equal((calls[0]!.init.headers as Record<string, string>)["x-test"], "1");
    assert.equal(calls[0]!.init.credentials, "include");
  });
});

describe("walkPages", () => {
  const page = (items: string[], next: string | null) => json({ items, page: { limit: 2, total: 5, next_cursor: next } });

  it("follows next_cursor until it is null and keeps the other filters", async () => {
    const cursors: Array<string | null> = [];
    const { fetch, calls } = stub((url) => {
      const cursor = new URL(url).searchParams.get("cursor");
      cursors.push(cursor);
      if (cursor === null) return page(["a", "b"], "c1");
      if (cursor === "c1") return page(["c", "d"], "c2");
      return page(["e"], null);
    });
    const client = new VyronClient({ baseUrl: "http://h", fetch });
    const seen: string[] = [];
    for await (const result of walkPages(client, "list_entities", { query: { kind: ["language"], limit: 2 } })) {
      seen.push(...(result.items as unknown as string[]));
    }
    assert.deepEqual(seen, ["a", "b", "c", "d", "e"]);
    assert.deepEqual(cursors, [null, "c1", "c2"]);
    for (const call of calls) {
      assert.equal(new URL(call.url).searchParams.get("kind"), "language");
      assert.equal(new URL(call.url).searchParams.get("limit"), "2");
    }
  });

  it("stops at maxPages", async () => {
    let n = 0;
    const { fetch } = stub(() => page(["x"], `c${(n += 1)}`));
    const client = new VyronClient({ baseUrl: "http://h", fetch });
    let pages = 0;
    for await (const _ of walkPages(client, "list_entities", {}, { maxPages: 3 })) pages += 1;
    assert.equal(pages, 3);
    assert.equal(n, 3);
  });

  it("refuses a repeated cursor", async () => {
    const { fetch } = stub(() => page(["x"], "same"));
    const client = new VyronClient({ baseUrl: "http://h", fetch });
    await assert.rejects(
      (async () => {
        for await (const _ of walkPages(client, "list_entities", {})) void _;
      })(),
      (e: unknown) => e instanceof ApiRequestError && /repeated a cursor/.test(e.message),
    );
  });
});
