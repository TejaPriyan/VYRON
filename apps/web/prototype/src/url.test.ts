import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { readPageState, writePageState } from "./url.ts";

const ID = "efb7327d-c6ce-5bbb-95ba-9eb5e052d588";

describe("page address", () => {
  it("reads the default page from an empty query", () => {
    const { state, issues } = readPageState("");
    assert.equal(state.view, "map");
    assert.equal(state.map.focus, null);
    assert.deepEqual(issues, []);
    assert.equal(writePageState(state), "");
  });

  it("round-trips focus, kinds, search text and the outline view", () => {
    const search = `?focus=${ID}&kind=language&kind=library&q=type+script&view=outline`;
    const { state, issues } = readPageState(search);
    assert.deepEqual(issues, []);
    assert.equal(state.map.focus, ID);
    assert.deepEqual(state.map.kinds, ["language", "library"]);
    assert.equal(state.map.query, "type script");
    assert.equal(state.view, "outline");
    assert.deepEqual(readPageState(writePageState(state)).state, state);
  });

  it("writes the same address for the same state", () => {
    const a = readPageState(`?q=react&focus=${ID.toUpperCase()}&utm_source=x`).state;
    const b = readPageState(`?focus=${ID}&q=react`).state;
    assert.equal(writePageState(a), writePageState(b));
    assert.doesNotMatch(writePageState(a), /utm_source/);
  });

  it("reports what it drops instead of throwing", () => {
    const { state, issues } = readPageState("?focus=not-an-id&kind=spaceship&depth=99&view=cube");
    assert.equal(state.map.focus, null);
    assert.deepEqual(state.map.kinds, []);
    assert.equal(state.view, "map");
    assert.equal(issues.length, 4);
  });

  it("says plainly that other map modes are not in the prototype", () => {
    const { state, issues } = readPageState("?mode=timeline");
    assert.equal(state.map.mode, "ecosystem");
    assert.equal(issues.length, 1);
    assert.match(issues[0] ?? "", /not part of the prototype/);
  });
});
