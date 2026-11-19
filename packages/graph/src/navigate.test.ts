import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { nextInDirection, readingOrder } from "./navigate.ts";

const grid = [
  { id: "a", x: 0, y: 0 }, { id: "b", x: 100, y: 0 }, { id: "c", x: 200, y: 0 },
  { id: "d", x: 0, y: 100 }, { id: "e", x: 100, y: 100 }, { id: "f", x: 200, y: 100 },
  { id: "g", x: 0, y: 200 }, { id: "h", x: 100, y: 200 }, { id: "i", x: 200, y: 200 },
]; // prettier-ignore

describe("nextInDirection", () => {
  it("moves to the neighbour straight ahead", () => {
    assert.equal(nextInDirection(grid, "e", "left"), "d");
    assert.equal(nextInDirection(grid, "e", "right"), "f");
    assert.equal(nextInDirection(grid, "e", "up"), "b");
    assert.equal(nextInDirection(grid, "e", "down"), "h");
  });

  it("stays put (null) when nothing lies that way", () => {
    assert.equal(nextInDirection(grid, "a", "left"), null);
    assert.equal(nextInDirection(grid, "a", "up"), null);
    assert.equal(nextInDirection(grid, "i", "right"), null);
  });

  it("prefers a node straight ahead to a slightly nearer one off to the side", () => {
    const items = [
      { id: "from", x: 0, y: 0 },
      { id: "ahead", x: 100, y: 0 },
      { id: "side", x: 80, y: 70 },
    ];
    assert.equal(nextInDirection(items, "from", "right"), "ahead");
  });

  it("ignores nodes behind or far to the side", () => {
    const items = [{ id: "from", x: 0, y: 0 }, { id: "up", x: 5, y: -100 }];
    assert.equal(nextInDirection(items, "from", "right"), null);
    assert.equal(nextInDirection(items, "from", "left"), null);
  });

  it("returns null for an unknown id and skips a node at the same spot", () => {
    assert.equal(nextInDirection(grid, "nope", "left"), null);
    assert.equal(nextInDirection([{ id: "x", x: 1, y: 1 }, { id: "y", x: 1, y: 1 }], "x", "left"), null);
  });

  it("breaks exact ties on id", () => {
    const items = [{ id: "from", x: 0, y: 0 }, { id: "z", x: 100, y: 0 }, { id: "m", x: 100, y: 0 }];
    assert.equal(nextInDirection(items, "from", "right"), "m");
  });

  it("can reach every node from every other by arrow keys", () => {
    for (const start of grid) {
      const seen = new Set([start.id]);
      const queue = [start.id];
      while (queue.length > 0) {
        const id = queue.shift() as string;
        for (const direction of ["left", "right", "up", "down"] as const) {
          const next = nextInDirection(grid, id, direction);
          if (next && !seen.has(next)) {
            seen.add(next);
            queue.push(next);
          }
        }
      }
      assert.equal(seen.size, grid.length, `from ${start.id}`);
    }
  });
});

describe("readingOrder", () => {
  it("goes top to bottom, then left to right, then by id", () => {
    assert.deepEqual(readingOrder(grid), ["a", "b", "c", "d", "e", "f", "g", "h", "i"]);
    assert.deepEqual(readingOrder([{ id: "b", x: 1, y: 1 }, { id: "a", x: 1, y: 1 }, { id: "c", x: 0, y: 0 }]), ["c", "a", "b"]);
  });
});
