// Run: node --test packages/graph/src/lod.test.ts
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { seedPosition } from "./layout-seed.ts";
import { LOD, clampZoom, detailLevel, edgeIsVisible, fitView, isVisible, labelBudget, placeEdgeLabels, placeLabels, selectLabels } from "./lod.ts";
import type { EdgeLabelCandidate, LabelCandidate, LabelObstacle, PlacedLabelCandidate } from "./lod.ts";

const view = { x: 0, y: 0, width: 800, height: 600 };
const label = (id: string, x: number, y: number, importance: number, width = 60, height = 14): LabelCandidate => ({ id, x, y, width, height, importance });

describe("detailLevel", () => {
  it("uses the thresholds that docs/FRONTEND.md documents", () => {
    assert.equal(LOD.nodesFromZoom, 1.25);
    assert.equal(LOD.labelsFromZoom, 2.5);
    assert.equal(LOD.minZoom, 0.25);
    assert.equal(LOD.maxZoom, 16);
    assert.deepEqual([1, 1.5, 3].map(detailLevel), ["clusters", "nodes", "labelled"]);
  });
  it("switches at the documented thresholds", () => {
    assert.equal(detailLevel(LOD.minZoom), "clusters");
    assert.equal(detailLevel(LOD.nodesFromZoom - 0.001), "clusters");
    assert.equal(detailLevel(LOD.nodesFromZoom), "nodes");
    assert.equal(detailLevel(LOD.labelsFromZoom - 0.001), "nodes");
    assert.equal(detailLevel(LOD.labelsFromZoom), "labelled");
    assert.equal(detailLevel(LOD.maxZoom * 10), "labelled");
  });
  it("treats a non-finite zoom as the whole-graph view", () => {
    assert.equal(clampZoom(Number.NaN), 1);
    assert.equal(detailLevel(Number.NaN), "clusters");
    assert.equal(clampZoom(0), LOD.minZoom);
    assert.equal(clampZoom(1e9), LOD.maxZoom);
  });
});

describe("culling", () => {
  it("keeps points inside the viewport plus a margin", () => {
    assert.ok(isVisible({ x: 10, y: 10 }, view));
    assert.ok(isVisible({ x: -20, y: 300 }, view));
    assert.ok(!isVisible({ x: -40, y: 300 }, view));
    assert.ok(!isVisible({ x: 400, y: 700 }, view));
  });
  it("draws an edge that crosses the viewport even when both ends are outside it", () => {
    assert.ok(edgeIsVisible({ x: -500, y: 300 }, { x: 1300, y: 300 }, view));
    assert.ok(!edgeIsVisible({ x: -500, y: 300 }, { x: -100, y: 400 }, view));
    assert.ok(edgeIsVisible({ x: 10, y: 10 }, { x: 5000, y: 5000 }, view));
  });
});

describe("fitView", () => {
  it("centres the bounds and uses the tighter axis", () => {
    const fit = fitView({ x: -100, y: -50, width: 200, height: 100 }, { width: 548, height: 448 }, 24);
    assert.equal(fit.centerX, 0);
    assert.equal(fit.centerY, 0);
    assert.equal(fit.zoom, 2.5); // (548-48)/200 = 2.5 on x; (448-48)/100 = 4 on y
  });
  it("zooms in to label level on a single point and clamps huge scales", () => {
    assert.equal(fitView({ x: 5, y: 5, width: 0, height: 0 }, { width: 800, height: 600 }).zoom, LOD.labelsFromZoom);
    assert.equal(fitView({ x: 0, y: 0, width: 0.0001, height: 0.0001 }, { width: 800, height: 600 }).zoom, LOD.maxZoom);
  });
  it("survives a viewport smaller than its padding", () => {
    const fit = fitView({ x: 0, y: 0, width: 10, height: 10 }, { width: 20, height: 20 }, 48);
    assert.ok(Number.isFinite(fit.zoom) && fit.zoom >= LOD.minZoom);
  });
});

describe("selectLabels", () => {
  it("keeps the more important of two overlapping labels", () => {
    assert.deepEqual(selectLabels([label("low", 100, 100, 1), label("high", 110, 102, 9)], { budget: 10 }), ["high"]);
  });
  it("keeps labels that do not collide", () => {
    const ids = selectLabels([label("a", 0, 0, 1), label("b", 200, 0, 2), label("c", 0, 100, 3)], { budget: 10 });
    assert.deepEqual(ids, ["c", "b", "a"]);
  });
  it("respects the budget", () => {
    const many = Array.from({ length: 50 }, (_, i) => label(`n${String(i).padStart(2, "0")}`, i * 100, 0, i));
    assert.equal(selectLabels(many, { budget: 7 }).length, 7);
    assert.deepEqual(selectLabels(many, { budget: 0 }), []);
  });
  it("places pinned labels first, even over a more important neighbour", () => {
    const ids = selectLabels([label("star", 100, 100, 99), label("selected", 105, 101, 0)], { budget: 10, pinned: new Set(["selected"]) });
    assert.deepEqual(ids, ["selected"]);
  });
  it("lets two pinned labels overlap rather than hide either", () => {
    const ids = selectLabels([label("a", 100, 100, 1), label("b", 105, 101, 2)], { budget: 10, pinned: new Set(["a", "b"]) });
    assert.deepEqual([...ids].sort(), ["a", "b"]);
  });
  it("is deterministic when importance ties", () => {
    const input = [label("b", 100, 100, 5), label("a", 105, 100, 5), label("c", 110, 100, 5)];
    assert.deepEqual(selectLabels(input, { budget: 3 }), ["a"]);
    assert.deepEqual(selectLabels([...input].reverse(), { budget: 3 }), ["a"]);
  });
  it("skips candidates with non-finite geometry", () => {
    assert.deepEqual(selectLabels([label("bad", Number.NaN, 0, 9), label("ok", 0, 0, 1)], { budget: 5 }), ["ok"]);
  });
  it("requires clear space between neighbours (gap)", () => {
    const close = [label("a", 0, 0, 2, 60, 14), label("b", 62, 0, 1, 60, 14)];
    assert.deepEqual(selectLabels(close, { budget: 5, gap: 4 }), ["a"]);
    assert.deepEqual(selectLabels(close, { budget: 5, gap: 0 }), ["a", "b"]);
  });
});

describe("labelBudget", () => {
  it("grows with detail and area, within hard bounds", () => {
    const screen = { width: 1440, height: 900 };
    assert.ok(labelBudget("clusters", screen) < labelBudget("nodes", screen));
    assert.ok(labelBudget("nodes", screen) < labelBudget("labelled", screen));
    assert.equal(labelBudget("labelled", { width: 0, height: 0 }), 4);
    assert.equal(labelBudget("labelled", { width: 9000, height: 9000 }), 120);
  });
});

describe("seedPosition", () => {
  const ids = Array.from({ length: 2000 }, (_, i) => `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`);
  it("is a pure function of the id", () => {
    assert.deepEqual(seedPosition(ids[0]!), seedPosition(ids[0]!));
    assert.notDeepEqual(seedPosition(ids[0]!), seedPosition(ids[1]!));
  });
  it("stays inside the unit disc and does not collide", () => {
    const seen = new Set<string>();
    for (const id of ids) {
      const { x, y } = seedPosition(id);
      assert.ok(Math.hypot(x, y) <= 1 + 1e-12);
      seen.add(`${x.toFixed(9)},${y.toFixed(9)}`);
    }
    assert.equal(seen.size, ids.length);
  });
  it("spreads sequential ids over the whole disc", () => {
    const quadrants = [0, 0, 0, 0];
    let radius = 0;
    for (const id of ids) {
      const { x, y } = seedPosition(id);
      quadrants[(x >= 0 ? 1 : 0) + (y >= 0 ? 2 : 0)]! += 1;
      radius += Math.hypot(x, y);
    }
    for (const q of quadrants) assert.ok(q / ids.length > 0.2, `quadrant share ${q / ids.length}`);
    const mean = radius / ids.length;
    assert.ok(mean > 0.6 && mean < 0.73, `mean radius ${mean} (a uniform disc gives 2/3)`);
  });
});

describe("placeLabels", () => {
  const mark = (id: string, x: number, y: number, importance = 1, width = 60): PlacedLabelCandidate => ({ id, x, y, radius: 8, width, height: 14, importance });
  const box = (id: string, x: number, y: number, reach = 10): LabelObstacle => ({ id, x: x - reach, y: y - reach, width: 2 * reach, height: 2 * reach });
  const sideOf = (placements: ReturnType<typeof placeLabels>, id: string) => placements.find((p) => p.id === id)?.side;

  it("puts a label to the right of its mark when that is free", () => {
    const [p] = placeLabels([mark("a", 100, 100)], { budget: 5 });
    assert.deepEqual({ id: p?.id, side: p?.side, x: p?.x, y: p?.y, anchor: p?.anchor }, { id: "a", side: "right", x: 113, y: 100, anchor: "start" });
    assert.deepEqual(p?.rect, { x: 113, y: 93, width: 60, height: 14 });
  });
  it("moves a label to the left when a neighbouring mark sits to its right", () => {
    const marks = [mark("a", 100, 100), mark("b", 150, 100)];
    const obstacles = marks.map((m) => box(m.id, m.x, m.y));
    const out = placeLabels([marks[0]!], { budget: 5, obstacles });
    assert.equal(sideOf(out, "a"), "left");
    assert.equal(out[0]?.anchor, "end");
    assert.equal(out[0]?.x, 87);
  });
  it("never counts a label's own mark as an obstacle", () => {
    const out = placeLabels([mark("a", 100, 100)], { budget: 5, obstacles: [box("a", 100, 100)] });
    assert.equal(sideOf(out, "a"), "right");
  });
  it("goes above and then below when both horizontal sides are blocked", () => {
    const blockers = [box("l", 40, 100, 14), box("r", 160, 100, 25)];
    const above = placeLabels([mark("a", 100, 100)], { budget: 5, obstacles: blockers });
    assert.equal(sideOf(above, "a"), "above");
    const below = placeLabels([mark("a", 100, 100)], { budget: 5, obstacles: [...blockers, box("u", 100, 78, 12)] });
    assert.equal(sideOf(below, "a"), "below");
  });
  it("skips an unpinned label that has no free side and still places a pinned one", () => {
    const ring = [box("l", 60, 100, 30), box("r", 150, 100, 30), box("u", 100, 60, 30), box("d", 100, 140, 30)];
    assert.deepEqual(placeLabels([mark("a", 100, 100)], { budget: 5, obstacles: ring }), []);
    const forced = placeLabels([mark("a", 100, 100)], { budget: 5, obstacles: ring, pinned: new Set(["a"]) });
    assert.deepEqual(forced.map((p) => [p.id, p.side]), [["a", "right"]]);
  });
  it("puts a forced label where it covers least, not just on the first side", () => {
    // Every side clashes. Right is covered completely, above and below by a strip each, left by a speck.
    const blockers = [box("big", 143, 100, 30), box("speck", 40, 100, 4)];
    assert.deepEqual(placeLabels([mark("a", 100, 100)], { budget: 5, obstacles: blockers }), []);
    const forced = placeLabels([mark("a", 100, 100)], { budget: 5, obstacles: blockers, pinned: new Set(["a"]) });
    assert.deepEqual(forced.map((p) => [p.id, p.side]), [["a", "left"]]);
  });
  it("counts text cut off by the bounds as covered when it has to place a pinned label", () => {
    // Right, above and below fall outside a thin strip; left is inside it but touches a mark, so it is the least bad.
    const bounds = { x: 0, y: 90, width: 130, height: 20 };
    const forced = placeLabels([mark("a", 100, 100)], { budget: 5, bounds, pinned: new Set(["a"]), obstacles: [box("m", 57, 100, 10)] });
    assert.deepEqual(forced.map((p) => [p.id, p.side]), [["a", "left"]]);
  });
  it("still takes the first side when every side covers the same", () => {
    const ring = [box("l", 60, 100, 30), box("r", 150, 100, 30), box("u", 100, 60, 30), box("d", 100, 140, 30)];
    const forced = placeLabels([mark("a", 100, 100)], { budget: 5, obstacles: ring, pinned: new Set(["a"]) });
    assert.equal(sideOf(forced, "a"), "right");
  });
  it("starts a label beside a wide mark beyond its sideways reach, not its height", () => {
    // A region 200 wide and 40 high: above and below are clear of it at 20 px, left and right at 100 px.
    const region: PlacedLabelCandidate = { id: "r", x: 300, y: 100, radius: 20, radiusX: 100, width: 60, height: 14, importance: 1 };
    const [right] = placeLabels([region], { budget: 5, sides: ["right"], offset: 6 });
    assert.equal(right?.rect.x, 300 + 100 + 6);
    const [left] = placeLabels([region], { budget: 5, sides: ["left"], offset: 6 });
    assert.equal(left ? left.rect.x + left.rect.width : 0, 300 - 100 - 6);
    const [above] = placeLabels([region], { budget: 5, sides: ["above"], offset: 6 });
    assert.equal(above ? above.rect.y + above.rect.height : 0, 100 - 20 - 6);
    const withoutReach: PlacedLabelCandidate = { id: "r", x: 300, y: 100, radius: 20, width: 60, height: 14, importance: 1 };
    const [plain] = placeLabels([withoutReach], { budget: 5, sides: ["right"], offset: 6 });
    assert.equal(plain?.rect.x, 300 + 20 + 6);
  });
  it("skips a candidate whose sideways reach is not a number", () => {
    assert.deepEqual(placeLabels([{ id: "bad", x: 0, y: 0, radius: 5, radiusX: Number.NaN, width: 40, height: 14, importance: 1 }], { budget: 5 }), []);
  });
  it("keeps two labels from covering each other by moving the less important one", () => {
    const out = placeLabels([mark("big", 100, 100, 9), mark("small", 110, 100, 1)], { budget: 5 });
    assert.equal(out.length, 2);
    assert.equal(sideOf(out, "big"), "right");
    assert.notEqual(sideOf(out, "small"), "right");
  });
  it("respects the budget and is deterministic when importance ties", () => {
    const many = Array.from({ length: 30 }, (_, i) => mark(`n${String(i).padStart(2, "0")}`, i * 200, 0, 1));
    assert.equal(placeLabels(many, { budget: 4 }).length, 4);
    const input = [mark("b", 100, 100, 5), mark("a", 104, 100, 5)];
    assert.deepEqual(placeLabels(input, { budget: 5 }), placeLabels([...input].reverse(), { budget: 5 }));
  });
  it("honours the side order it is given", () => {
    const [p] = placeLabels([mark("a", 100, 100)], { budget: 5, sides: ["below", "right"] });
    assert.equal(p?.side, "below");
    assert.equal(p?.anchor, "middle");
    assert.equal(p?.y, 100 + 8 + 5 + 7);
  });
  it("keeps a label inside the bounds by choosing another side", () => {
    const bounds = { x: 0, y: 0, width: 150, height: 200 };
    const [p] = placeLabels([mark("edge", 120, 100)], { budget: 5, bounds });
    assert.equal(p?.side, "left");
  });
  it("skips an unpinned label that fits nowhere inside the bounds", () => {
    const bounds = { x: 0, y: 0, width: 40, height: 40 };
    assert.deepEqual(placeLabels([mark("wide", 20, 20, 1, 200)], { budget: 5, bounds }), []);
  });
  it("ignores candidates and obstacles with non-finite geometry", () => {
    const out = placeLabels([mark("bad", Number.NaN, 0), mark("ok", 0, 0)], {
      budget: 5,
      obstacles: [{ x: Number.NaN, y: 0, width: 10, height: 10 }],
    });
    assert.deepEqual(out.map((p) => p.id), ["ok"]);
  });
});

describe("placeEdgeLabels", () => {
  const edge = (id: string, x1: number, y1: number, x2: number, y2: number, width = 70, height = 14): EdgeLabelCandidate => ({ id, x1, y1, x2, y2, width, height });
  const overlap = (a: { x: number; y: number }, b: { x: number; y: number }, w = 70, h = 14): boolean => Math.abs(a.x - b.x) < w && Math.abs(a.y - b.y) < h;

  it("sets a label beside the middle of a free line, clear of the line itself", () => {
    const placed = placeEdgeLabels([edge("e", 0, 100, 200, 100)]);
    const point = placed.get("e");
    assert.ok(point);
    assert.equal(point.x, 100);
    assert.ok(Math.abs(point.y - 100) >= 7, "label rectangle must not sit on the line");
  });
  it("keeps clear of a blocker by sliding along the line or switching side", () => {
    const blocker = { x: 60, y: 80, width: 80, height: 17 };
    const point = placeEdgeLabels([edge("e", 0, 100, 200, 100)], { blockers: [blocker] }).get("e");
    assert.ok(point);
    const rect = { x: point.x - 35, y: point.y - 7, width: 70, height: 14 };
    const clear = rect.x >= blocker.x + blocker.width || blocker.x >= rect.x + rect.width || rect.y >= blocker.y + blocker.height || blocker.y >= rect.y + rect.height;
    assert.ok(clear);
  });
  it("never lets two labels overlap and drops the one that has no room", () => {
    const crowded = [edge("a", 100, 100, 110, 100), edge("b", 100, 101, 110, 101), edge("c", 100, 102, 110, 102)];
    const placed = placeEdgeLabels(crowded);
    const points = [...placed.values()];
    for (let i = 0; i < points.length; i += 1) for (let j = i + 1; j < points.length; j += 1) assert.ok(!overlap(points[i]!, points[j]!));
    assert.ok(placed.has("a"));
  });
  it("omits a label that cannot fit inside the bounds", () => {
    const placed = placeEdgeLabels([edge("e", 0, 10, 20, 10, 200)], { bounds: { x: 0, y: 0, width: 100, height: 100 } });
    assert.equal(placed.size, 0);
  });
  it("places labels along a steep line on either side of it", () => {
    const point = placeEdgeLabels([edge("v", 100, 0, 100, 200)]).get("v");
    assert.ok(point);
    assert.ok(Math.abs(point.x - 100) >= 35, "a horizontal label beside a vertical line sits a half-width away");
  });
  it("skips degenerate or non-finite lines without throwing", () => {
    const placed = placeEdgeLabels([edge("zero", 5, 5, 5, 5), edge("nan", Number.NaN, 0, 10, 10), edge("ok", 0, 0, 100, 0)]);
    assert.deepEqual([...placed.keys()], ["ok"]);
  });
  it("is deterministic for the same input", () => {
    const input = [edge("a", 0, 0, 120, 60), edge("b", 0, 20, 120, 80)];
    assert.deepEqual([...placeEdgeLabels(input)], [...placeEdgeLabels(input)]);
  });
});
