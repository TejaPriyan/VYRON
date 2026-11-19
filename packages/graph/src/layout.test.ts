import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { computeLayout } from "./layout.ts";
import type { LayoutLink, LayoutNode } from "./layout.ts";

/** Three groups of five, a ring inside each group and one link between neighbouring groups. */
function threeGroups(): { nodes: LayoutNode[]; links: LayoutLink[] } {
  const nodes: LayoutNode[] = [];
  const links: LayoutLink[] = [];
  for (const group of ["g1", "g2", "g3"]) {
    for (let i = 0; i < 5; i += 1) nodes.push({ id: `${group}-n${i}`, group });
    for (let i = 0; i < 5; i += 1) links.push({ source: `${group}-n${i}`, target: `${group}-n${(i + 1) % 5}` });
  }
  links.push({ source: "g1-n0", target: "g2-n0" }, { source: "g2-n2", target: "g3-n2" });
  return { nodes, links };
}

const dist = (a: { x: number; y: number }, b: { x: number; y: number }): number => Math.hypot(a.x - b.x, a.y - b.y);

describe("computeLayout", () => {
  it("gives the same positions for the same input, whatever order it arrives in", () => {
    const { nodes, links } = threeGroups();
    const first = computeLayout(nodes, links);
    const again = computeLayout(nodes, links);
    const shuffled = computeLayout([...nodes].reverse(), [...links].reverse());
    const flat = (l: ReturnType<typeof computeLayout>): string => JSON.stringify([...l.positions].sort(([a], [b]) => (a < b ? -1 : 1)));
    assert.equal(flat(first), flat(again));
    assert.equal(flat(first), flat(shuffled));
  });

  it("places every node once, at a finite position, centred on the origin", () => {
    const { nodes, links } = threeGroups();
    const { positions } = computeLayout(nodes, links);
    assert.equal(positions.size, nodes.length);
    let sx = 0;
    let sy = 0;
    for (const p of positions.values()) {
      assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
      sx += p.x;
      sy += p.y;
    }
    assert.ok(Math.abs(sx / positions.size) < 1e-9 && Math.abs(sy / positions.size) < 1e-9);
  });

  it("keeps every pair of nodes apart by the clearance", () => {
    const { nodes, links } = threeGroups();
    const { positions } = computeLayout(nodes, links);
    const all = [...positions.values()];
    let min = Number.POSITIVE_INFINITY;
    for (let i = 0; i < all.length; i += 1) for (let j = i + 1; j < all.length; j += 1) min = Math.min(min, dist(all[i]!, all[j]!));
    assert.ok(min >= 0.6 - 1e-6, `closest pair is ${min}`);
  });

  it("separates clusters: members sit nearer each other than other clusters' members", () => {
    const { nodes, links } = threeGroups();
    const { positions } = computeLayout(nodes, links);
    const group = new Map(nodes.map((n) => [n.id, n.group]));
    const within: number[] = [];
    const between: number[] = [];
    const ids = [...positions.keys()];
    for (let i = 0; i < ids.length; i += 1) {
      for (let j = i + 1; j < ids.length; j += 1) {
        const d = dist(positions.get(ids[i]!)!, positions.get(ids[j]!)!);
        (group.get(ids[i]!) === group.get(ids[j]!) ? within : between).push(d);
      }
    }
    const mean = (xs: number[]): number => xs.reduce((a, b) => a + b, 0) / xs.length;
    assert.ok(mean(within) < 0.6 * mean(between), `within ${mean(within).toFixed(2)}, between ${mean(between).toFixed(2)}`);
  });

  it("keeps the anchors of different groups clear of each other", () => {
    const { nodes, links } = threeGroups();
    const { anchors } = computeLayout(nodes, links);
    const all = [...anchors.values()];
    assert.equal(all.length, 3);
    for (let i = 0; i < all.length; i += 1) for (let j = i + 1; j < all.length; j += 1) assert.ok(dist(all[i]!, all[j]!) >= 3);
  });

  it("pulls linked nodes closer than unlinked ones in the same group", () => {
    const nodes = ["a", "b", "c", "d", "e", "f"].map((id) => ({ id, group: "g" }));
    const links = [{ source: "a", target: "b" }, { source: "b", target: "c" }, { source: "d", target: "e" }, { source: "e", target: "f" }];
    const { positions } = computeLayout(nodes, links);
    const linked = (dist(positions.get("a")!, positions.get("b")!) + dist(positions.get("d")!, positions.get("e")!)) / 2;
    const unlinked = (dist(positions.get("a")!, positions.get("d")!) + dist(positions.get("c")!, positions.get("f")!)) / 2;
    assert.ok(linked < unlinked, `linked ${linked.toFixed(2)} vs unlinked ${unlinked.toFixed(2)}`);
  });

  it("copes with no nodes, one node, repeated ids, self-links and links to nodes that are not there", () => {
    assert.equal(computeLayout([], []).positions.size, 0);
    const one = computeLayout([{ id: "x", group: "g" }], []);
    assert.deepEqual(one.positions.get("x"), { x: 0, y: 0 });
    const odd = computeLayout(
      [{ id: "a", group: "g" }, { id: "a", group: "other" }, { id: "b", group: "g" }],
      [{ source: "a", target: "a" }, { source: "a", target: "missing" }, { source: "a", target: "b" }, { source: "b", target: "a" }],
    );
    assert.equal(odd.positions.size, 2);
    assert.ok([...odd.positions.values()].every((p) => Number.isFinite(p.x) && Number.isFinite(p.y)));
  });

  it("separates nodes that start on the same spot", () => {
    const { positions } = computeLayout([{ id: "a", group: "g" }, { id: "b", group: "g" }], [], { iterations: 1 });
    assert.ok(dist(positions.get("a")!, positions.get("b")!) >= 0.6 - 1e-6);
  });

  it("rejects an unusable link length", () => {
    for (const linkLength of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      assert.throws(() => computeLayout([{ id: "a", group: "g" }], [], { linkLength }), RangeError);
    }
  });

  it("handles a few hundred nodes quickly enough to run on page load", () => {
    const nodes: LayoutNode[] = [];
    const links: LayoutLink[] = [];
    for (let i = 0; i < 300; i += 1) {
      nodes.push({ id: `n${i}`, group: `g${i % 12}` });
      if (i >= 12) links.push({ source: `n${i}`, target: `n${i - 12}` });
    }
    const start = performance.now();
    const { positions } = computeLayout(nodes, links, { iterations: 120 });
    const elapsed = performance.now() - start;
    assert.equal(positions.size, 300);
    assert.ok(elapsed < 5000, `took ${elapsed.toFixed(0)} ms`); // a generous ceiling: this checks "not quadratic blow-up", not speed
  });
});
