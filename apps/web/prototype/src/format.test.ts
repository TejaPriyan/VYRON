import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { describeRank, formatEventDate, splitSentence } from "./format.ts";

describe("formatEventDate", () => {
  it("shows a date at the precision it is known to", () => {
    assert.equal(formatEventDate("1991-01-01", "year"), "1991");
    assert.equal(formatEventDate("2008-04-01", "month"), "Apr 2008");
    assert.equal(formatEventDate("2013-03-20", "day"), "20 Mar 2013");
    assert.equal(formatEventDate("1995-01-01", "decade"), "1990s");
  });

  it("returns what it cannot read unchanged", () => {
    assert.equal(formatEventDate("sometime", "year"), "sometime");
    assert.equal(formatEventDate("2013-13-01", "month"), "2013-13-01");
  });
});

describe("splitSentence", () => {
  it("separates the other entity's name so it can be a link", () => {
    assert.deepEqual(splitSentence("React is built on JavaScript.", "JavaScript"), { before: "React is built on ", name: "JavaScript", after: "." });
  });

  it("uses the last occurrence when the name appears earlier in the sentence", () => {
    assert.deepEqual(splitSentence("Node is related to Node.", "Node"), { before: "Node is related to ", name: "Node", after: "." });
  });

  it("falls back to plain text for a sentence that does not end with the name", () => {
    assert.deepEqual(splitSentence("Something else entirely.", "React"), { before: "Something else entirely.", name: "", after: "" });
    assert.deepEqual(splitSentence("React.", "React"), { before: "React.", name: "", after: "" });
  });
});

describe("describeRank", () => {
  it("ranks by value, highest first", () => {
    assert.equal(describeRank(0.5, [0.5, 0.3, 0.2]), "Ranked 1 of 3");
    assert.equal(describeRank(0.2, [0.5, 0.3, 0.2]), "Ranked 3 of 3");
  });

  it("lets ties share the better rank and says so", () => {
    assert.equal(describeRank(0.3, [0.5, 0.3, 0.3, 0.1]), "Ranked 2 of 4, tied");
  });
});
