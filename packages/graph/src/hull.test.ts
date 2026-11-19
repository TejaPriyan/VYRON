import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { convexHull, meanPoint, paddedHull, polygonPath } from "./hull.ts";

const p = (x: number, y: number) => ({ x, y });

describe("convexHull", () => {
  it("keeps the corners and drops interior and edge points", () => {
    const hull = convexHull([p(0, 0), p(4, 0), p(4, 4), p(0, 4), p(2, 2), p(2, 0), p(0, 2)]);
    assert.deepEqual(
      new Set(hull.map((q) => `${q.x},${q.y}`)),
      new Set(["0,0", "4,0", "4,4", "0,4"]),
    );
    assert.equal(hull.length, 4);
  });

  it("returns fewer than three distinct points unchanged and sorted", () => {
    assert.deepEqual(convexHull([]), []);
    assert.deepEqual(convexHull([p(1, 1), p(1, 1)]), [p(1, 1)]);
    assert.deepEqual(convexHull([p(3, 0), p(1, 0)]), [p(1, 0), p(3, 0)]);
  });

  it("reduces collinear points to their two ends", () => {
    const hull = convexHull([p(0, 0), p(1, 1), p(2, 2), p(3, 3)]);
    assert.equal(hull.length, 2);
  });

  it("ignores points that are not finite numbers", () => {
    assert.equal(convexHull([p(0, 0), p(Number.NaN, 1), p(1, 0), p(0, 1)]).length, 3);
  });

  it("does not depend on input order", () => {
    const a = [p(0, 0), p(5, 1), p(3, 6), p(-2, 4), p(1, 2)];
    const b = [...a].reverse();
    assert.deepEqual(convexHull(a), convexHull(b));
  });
});

describe("paddedHull", () => {
  it("surrounds every centre by at least the radius", () => {
    const centres = [p(0, 0), p(10, 0), p(5, 8)];
    const hull = paddedHull(centres, 2);
    const inside = (point: { x: number; y: number }): boolean => {
      // Convex polygon, vertices in a consistent winding: the point is on the same side of every edge.
      let sign = 0;
      for (let i = 0; i < hull.length; i += 1) {
        const a = hull[i] as { x: number; y: number };
        const b = hull[(i + 1) % hull.length] as { x: number; y: number };
        const side = (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x);
        if (side !== 0) {
          if (sign === 0) sign = Math.sign(side);
          else if (Math.sign(side) !== sign) return false;
        }
      }
      return true;
    };
    for (const c of centres) assert.ok(inside(c));
    for (const c of centres) {
      // The centre is at least `radius * cos(pi / samples)` from every hull edge.
      for (let i = 0; i < hull.length; i += 1) {
        const a = hull[i] as { x: number; y: number };
        const b = hull[(i + 1) % hull.length] as { x: number; y: number };
        const length = Math.hypot(b.x - a.x, b.y - a.y);
        const distance = Math.abs((b.x - a.x) * (a.y - c.y) - (a.x - c.x) * (b.y - a.y)) / length;
        assert.ok(distance >= 2 * Math.cos(Math.PI / 20) - 1e-9, `edge ${i} is ${distance} from a centre`);
      }
    }
  });

  it("makes a disc for one centre and a capsule for two", () => {
    const disc = paddedHull([p(0, 0)], 3);
    assert.ok(disc.every((q) => Math.abs(Math.hypot(q.x, q.y) - 3) < 1e-9));
    const capsule = paddedHull([p(0, 0), p(10, 0)], 3);
    const xs = capsule.map((q) => q.x);
    assert.ok(Math.min(...xs) <= -2.99 && Math.max(...xs) >= 12.99);
    assert.ok(capsule.every((q) => Math.abs(q.y) <= 3 + 1e-9));
  });

  it("rejects a radius or sample count that cannot make a shape", () => {
    assert.throws(() => paddedHull([p(0, 0)], 0), RangeError);
    assert.throws(() => paddedHull([p(0, 0)], 1, 2), RangeError);
  });
});

describe("polygonPath and meanPoint", () => {
  it("writes a closed path, and nothing for no points", () => {
    assert.equal(polygonPath([p(0, 0), p(10.04, 0), p(5, 8)]), "M0 0L10 0L5 8Z");
    assert.equal(polygonPath([]), "");
  });

  it("averages points", () => {
    assert.deepEqual(meanPoint([p(0, 0), p(4, 2)]), p(2, 1));
    assert.equal(meanPoint([]), null);
  });
});
