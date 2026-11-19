import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { tokens } from "../../ui/src/tokens.ts";
import type { ShapeKey } from "../../ui/src/tokens.ts";
import { GLYPH_REACH, glyphPath } from "./glyph.ts";

const numbers = (path: string): number[] => (path.match(/-?\d+(?:\.\d+)?/g) ?? []).map(Number);

/** Vertices of a straight-sided path ("M x y L x y ... Z"). */
function vertices(path: string): Array<[number, number]> {
  const n = numbers(path);
  const out: Array<[number, number]> = [];
  for (let i = 0; i + 1 < n.length; i += 2) out.push([n[i] as number, n[i + 1] as number]);
  return out;
}

function area(points: Array<[number, number]>): number {
  let sum = 0;
  points.forEach(([x1, y1], i) => {
    const [x2, y2] = points[(i + 1) % points.length] as [number, number];
    sum += x1 * y2 - x2 * y1;
  });
  return Math.abs(sum) / 2;
}

describe("glyphPath", () => {
  const shapes = tokens.shapes as ReadonlyArray<ShapeKey>;

  it("draws every shape the design tokens name, as one closed path", () => {
    for (const shape of shapes) {
      const path = glyphPath(shape, 10);
      assert.match(path, /^M/, shape);
      assert.match(path, /Z$/, shape);
      assert.doesNotMatch(path, /NaN|Infinity|undefined/, shape);
    }
  });

  it("gives every shape its own outline", () => {
    assert.equal(new Set(shapes.map((shape) => glyphPath(shape, 10))).size, shapes.length);
  });

  it("stays inside the documented reach and is centred on the origin", () => {
    for (const shape of shapes) {
      const points = shape === "circle" ? [[10, 0], [-10, 0]] : vertices(glyphPath(shape, 10));
      for (const [x, y] of points as Array<[number, number]>) {
        assert.ok(Math.hypot(x, y) <= GLYPH_REACH * 10 + 0.01, `${shape} reaches ${Math.hypot(x, y)}`);
      }
      if (shape !== "circle") {
        const mean = points.reduce((sum, [x, y]) => [sum[0] + x, sum[1] + y], [0, 0]);
        assert.ok(Math.abs((mean[0] ?? 0) / points.length) < 1.5 && Math.abs((mean[1] ?? 0) / points.length) < 1.5, shape);
      }
    }
  });

  it("gives the straight-sided shapes about the visual weight of a circle", () => {
    const circle = Math.PI * 10 * 10;
    for (const shape of shapes.filter((s) => s !== "circle")) {
      const ratio = area(vertices(glyphPath(shape, 10))) / circle;
      assert.ok(ratio > 0.8 && ratio < 1.25, `${shape} has ${ratio.toFixed(2)} of a circle's area`);
    }
  });

  it("scales linearly with the radius", () => {
    for (const shape of shapes.filter((s) => s !== "circle")) {
      const small = numbers(glyphPath(shape, 5));
      const large = numbers(glyphPath(shape, 10));
      assert.equal(small.length, large.length);
      small.forEach((value, i) => assert.ok(Math.abs(value * 2 - (large[i] as number)) < 0.03, shape));
    }
  });

  it("rejects a radius that cannot be drawn", () => {
    for (const radius of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      assert.throws(() => glyphPath("circle", radius), RangeError);
    }
  });
});
