// Run: node --test packages/graph/src/map-state.test.ts
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ENTITY_KIND_VALUES, MAP_MODE_VALUES } from "../../types/src/api.ts";
import { MAX_QUERY_LENGTH, cleanQuery, emptyMapState, mapStateEquals, parseMapState, serializeMapState } from "./map-state.ts";

const parse = (query: string) => parseMapState(new URLSearchParams(query));
const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

describe("parseMapState", () => {
  it("returns the defaults for an empty query, with no issues", () => {
    const { state, issues } = parse("");
    assert.deepEqual(state, emptyMapState());
    assert.deepEqual(issues, []);
    assert.equal(serializeMapState(state), "");
  });

  it("reads every field", () => {
    const { state, issues } = parse(`mode=ai&focus=${ID}&kind=language&kind=ai_model&depth=2&year=2012&q=attention`);
    assert.deepEqual(issues, []);
    assert.deepEqual(state, { mode: "ai", focus: ID, kinds: ["language", "ai_model"], depth: 2, year: 2012, query: "attention" });
  });

  it("accepts every map mode and every entity kind the API defines", () => {
    for (const mode of MAP_MODE_VALUES) assert.equal(parse(`mode=${mode}`).state.mode, mode);
    assert.equal(parse(ENTITY_KIND_VALUES.map((k) => `kind=${k}`).join("&")).state.kinds.length, ENTITY_KIND_VALUES.length);
  });

  it("drops and reports invalid values instead of throwing", () => {
    const { state, issues } = parse("mode=bogus&focus=not-a-uuid&kind=gadget&depth=9&year=1800");
    assert.deepEqual(state, emptyMapState());
    assert.equal(issues.length, 5);
    for (const bad of ["depth=1.5", "depth=abc", "depth=-1", "year=2201", "year=19e2", "year="]) {
      assert.ok(parse(bad).issues.length >= 1, bad);
      assert.deepEqual(parse(bad).state, emptyMapState(), bad);
    }
  });

  it("keeps the valid parts of a partly valid link", () => {
    const { state, issues } = parse("mode=ai&depth=99&kind=language");
    assert.equal(state.mode, "ai");
    assert.deepEqual(state.kinds, ["language"]);
    assert.equal(state.depth, 1);
    assert.equal(issues.length, 1);
  });

  it("de-duplicates kinds and orders them as the API orders them", () => {
    const { state } = parse("kind=database&kind=language&kind=database&kind=technology");
    assert.deepEqual(state.kinds, ["technology", "language", "database"]);
  });

  it("normalises an upper-case entity id", () => {
    assert.equal(parse(`focus=${ID.toUpperCase()}`).state.focus, ID);
  });

  it("ignores parameters it does not own and uses the first of a repeated scalar", () => {
    const { state, issues } = parse("utm_source=x&fbclid=y&mode=ai&mode=research");
    assert.equal(state.mode, "ai");
    assert.deepEqual(issues, []);
  });

  it("cleans the search text", () => {
    const bidi = String.fromCodePoint(0x202e);
    const lineSep = String.fromCodePoint(0x2028);
    assert.equal(cleanQuery(`  rust ${bidi}lang\u0000\n\t  ${lineSep}ecosystem `), "rust lang ecosystem");
    assert.equal(cleanQuery("x".repeat(500)).length, MAX_QUERY_LENGTH);
    assert.equal(cleanQuery("   "), "");
  });

  it("never echoes a long or control-laden value in an issue", () => {
    const { issues } = parse(`mode=${encodeURIComponent("A".repeat(300) + "\u0007")}`);
    assert.equal(issues.length, 1);
    assert.ok(issues[0]!.length < 80, issues[0]);
    assert.doesNotMatch(issues[0]!, /\u0007/);
  });
});

describe("serializeMapState", () => {
  it("omits defaults and writes a stable order", () => {
    const state = { mode: "ai", focus: ID, kinds: ["framework", "language"], depth: 1, year: 2020, query: "gpu" } as const;
    assert.equal(serializeMapState(state), `mode=ai&focus=${ID}&kind=language&kind=framework&year=2020&q=gpu`);
  });

  it("round-trips", () => {
    const original = parse(`mode=dependencies&focus=${ID}&kind=library&depth=3&year=1999&q=c%2B%2B+%26+rust`).state;
    assert.deepEqual(parse(serializeMapState(original)).state, original);
  });

  it("is idempotent for arbitrary input", () => {
    let seed = 12345;
    const next = (n: number): number => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed % n;
    };
    const pool = ["mode=ai", "mode=bogus", `focus=${ID}`, "focus=zzz", "kind=language", "kind=nope", "depth=2", "depth=77", "year=2001", "year=3", "q=hello+world", "q=%00%0a", "utm=1"];
    for (let i = 0; i < 500; i += 1) {
      const query = Array.from({ length: next(8) }, () => pool[next(pool.length)]!).join("&");
      const once = serializeMapState(parse(query).state);
      const twice = serializeMapState(parse(once).state);
      assert.equal(twice, once, query);
      assert.deepEqual(parse(once).issues, [], `a canonical URL must parse cleanly: ${once}`);
    }
  });

  it("compares states by their canonical form", () => {
    assert.ok(mapStateEquals(parse("kind=language&kind=api").state, parse("kind=api&kind=language&kind=api").state));
    assert.ok(!mapStateEquals(parse("depth=2").state, parse("depth=3").state));
  });
});
