import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  addRead,
  DEFAULT_METRICS,
  dependencyOutline,
  edgePath,
  emptyGraph,
  layoutLayers,
  MAX_NODES,
  nodeStatus,
  packageId,
  stepFrom,
  type DependencyGraph,
  type DependencyRef,
  type PackageRead,
} from "./dependency-graph.ts";

// Made-up package names, like the stand-in registry's: nothing here describes a real package.
const dep = (name: string, requirement: string | null = null, readable = true): DependencyRef => ({ name, requirement, readable });

const read = (name: string, dependencies: ReadonlyArray<DependencyRef | string> = [], registry: "pypi" | "npm" = "pypi"): PackageRead => ({
  registry,
  name,
  version: "1.0.0",
  dependencies: dependencies.map((entry) => (typeof entry === "string" ? dep(entry) : entry)),
});

function build(...reads: PackageRead[]): DependencyGraph {
  let graph = emptyGraph();
  for (const next of reads) graph = addRead(graph, next).graph;
  return graph;
}

const id = (name: string): string => packageId("pypi", name);

describe("addRead", () => {
  it("makes the first package the root and lists its dependencies as unread nodes one step away", () => {
    const { graph, added, notDrawn } = addRead(emptyGraph(), read("fixture-web", [dep("fixture-router", ">=1.0"), "fixture-utils"]));
    assert.equal(graph.root, "pypi:fixture-web");
    assert.equal(added, 2);
    assert.equal(notDrawn, 0);
    const root = graph.nodes.get(id("fixture-web"));
    assert.deepEqual([root?.depth, root?.read, root?.version], [0, true, "1.0.0"]);
    const router = graph.nodes.get(id("fixture-router"));
    assert.deepEqual([router?.depth, router?.read, router?.readable, router?.version], [1, false, true, null]);
    assert.deepEqual(graph.edges.map((edge) => [edge.from, edge.to, edge.requirement]), [
      [id("fixture-web"), id("fixture-router"), ">=1.0"],
      [id("fixture-web"), id("fixture-utils"), null],
    ]);
  });

  it("does not change the graph it was given", () => {
    const first = addRead(emptyGraph(), read("fixture-web", ["fixture-router"])).graph;
    const before = JSON.stringify([...first.nodes], null, 0) + JSON.stringify(first.edges);
    addRead(first, read("fixture-router", ["fixture-utils"]));
    assert.equal(JSON.stringify([...first.nodes], null, 0) + JSON.stringify(first.edges), before);
  });

  it("opening a dependency fills in its own dependencies one step further out", () => {
    const graph = build(read("fixture-web", ["fixture-router"]), read("fixture-router", ["fixture-utils"]));
    assert.equal(graph.nodes.get(id("fixture-router"))?.read, true);
    assert.equal(graph.nodes.get(id("fixture-utils"))?.depth, 2);
    assert.equal(graph.root, id("fixture-web"));
  });

  it("keeps a package that two others depend on as one node with an edge from each", () => {
    const graph = build(
      read("fixture-web", ["fixture-router", "fixture-templates"]),
      read("fixture-router", ["fixture-utils"]),
      read("fixture-templates", ["fixture-utils"]),
    );
    assert.equal([...graph.nodes.keys()].filter((key) => key === id("fixture-utils")).length, 1);
    assert.equal(graph.nodes.size, 4);
    assert.deepEqual(
      graph.edges.filter((edge) => edge.to === id("fixture-utils")).map((edge) => edge.from).sort(),
      [id("fixture-router"), id("fixture-templates")],
    );
  });

  it("puts a package at its fewest steps from the root, whichever path it was met by first", () => {
    const graph = build(read("fixture-web", ["fixture-router", "fixture-utils"]), read("fixture-router", ["fixture-utils"]));
    assert.equal(graph.nodes.get(id("fixture-utils"))?.depth, 1);
  });

  it("moves a package closer when a later read finds a shorter path", () => {
    const longer = build(read("fixture-web", ["fixture-router"]), read("fixture-router", ["fixture-utils"]));
    assert.equal(longer.nodes.get(id("fixture-utils"))?.depth, 2);
    const shorter = addRead(longer, read("fixture-web", ["fixture-router", "fixture-utils"])).graph;
    assert.equal(shorter.nodes.get(id("fixture-utils"))?.depth, 1);
  });

  it("copes with a cycle: each package once, both edges, depths from the root", () => {
    const graph = build(read("fixture-cycle-a", ["fixture-cycle-b"]), read("fixture-cycle-b", ["fixture-cycle-a"]));
    assert.equal(graph.nodes.size, 2);
    assert.equal(graph.edges.length, 2);
    assert.deepEqual(
      [graph.nodes.get(id("fixture-cycle-a"))?.depth, graph.nodes.get(id("fixture-cycle-b"))?.depth],
      [0, 1],
    );
  });

  it("copes with a package that lists itself", () => {
    const { graph, added } = addRead(emptyGraph(), read("fixture-self", ["fixture-self", "fixture-utils"]));
    assert.equal(added, 1);
    assert.equal(graph.nodes.size, 2);
    assert.equal(layoutLayers(graph).nodes.length, 2);
    assert.equal(dependencyOutline(graph, layoutLayers(graph)).length, 3);
  });

  it("reading a package again replaces its edges instead of doubling them", () => {
    const once = build(read("fixture-web", ["fixture-router", "fixture-utils"]));
    const twice = addRead(once, read("fixture-web", ["fixture-router", "fixture-utils"])).graph;
    assert.equal(twice.edges.length, 2);
    assert.equal(twice.nodes.size, 3);
  });

  it("drops a dependency a newer read no longer lists, with whatever only it led to", () => {
    const before = build(
      read("fixture-web", ["fixture-router", "fixture-utils"]),
      read("fixture-router", ["fixture-markup"]),
    );
    assert.equal(before.nodes.has(id("fixture-markup")), true);
    const after = addRead(before, read("fixture-web", ["fixture-utils"])).graph;
    assert.deepEqual([...after.nodes.keys()], [id("fixture-web"), id("fixture-utils")]);
    assert.equal(after.edges.length, 1);
  });

  it("keeps a dependency that something else still lists when one dependent drops it", () => {
    const before = build(
      read("fixture-web", ["fixture-router", "fixture-templates"]),
      read("fixture-router", ["fixture-utils"]),
      read("fixture-templates", ["fixture-utils"]),
    );
    const after = addRead(before, read("fixture-router", [])).graph;
    assert.equal(after.nodes.has(id("fixture-utils")), true);
    assert.deepEqual(after.edges.filter((edge) => edge.to === id("fixture-utils")).map((edge) => edge.from), [id("fixture-templates")]);
  });

  it("lists a name once even when a package repeats it", () => {
    const { graph, added } = addRead(emptyGraph(), read("fixture-web", [dep("fixture-utils", ">=1"), dep("fixture-utils", "<2")]));
    assert.equal(added, 1);
    assert.equal(graph.edges.length, 1);
    assert.equal(graph.edges[0]?.requirement, ">=1");
  });

  it("marks a dependency the registry cannot serve by name as unreadable, and a later readable mention wins", () => {
    const first = addRead(emptyGraph(), read("fixture-web", [dep("fixture-direct", null, false)])).graph;
    assert.equal(nodeStatus(first.nodes.get(id("fixture-direct")) as never), "unreadable");
    const second = addRead(first, read("fixture-other", [dep("fixture-direct", ">=1", true)])).graph;
    assert.equal(second.nodes.get(id("fixture-direct"))?.readable, true);
    assert.equal(nodeStatus(second.nodes.get(id("fixture-direct")) as never), "unread");
  });

  it("does not turn a readable package unreadable because another one lists it as a link", () => {
    const first = addRead(emptyGraph(), read("fixture-web", [dep("fixture-direct", ">=1", true)])).graph;
    const second = addRead(first, read("fixture-other", [dep("fixture-direct", null, false)])).graph;
    assert.equal(second.nodes.get(id("fixture-direct"))?.readable, true);
  });

  it("keeps a package that was read even though nothing in the graph listed it", () => {
    const graph = build(read("fixture-web", ["fixture-router"]), read("fixture-elsewhere", ["fixture-utils"]));
    assert.equal(graph.nodes.get(id("fixture-elsewhere"))?.read, true);
    assert.equal(graph.nodes.has(id("fixture-utils")), true);
    assert.equal(graph.root, id("fixture-web"));
  });

  it("keeps the same name in two registries apart", () => {
    const graph = build(read("fixture-web", ["fixture-body"]), read("fixture-body", [], "npm"));
    assert.equal(graph.nodes.has("npm:fixture-body"), true);
    assert.equal(graph.nodes.get(id("fixture-body"))?.read, false);
    assert.equal(graph.nodes.get("npm:fixture-body")?.read, true);
  });

  it("stops adding nodes when the graph is full and says how many it left out", () => {
    const names = Array.from({ length: 5 }, (_, index) => `fixture-wide-${index}`);
    const { graph, added, notDrawn } = addRead(emptyGraph(), read("fixture-wide", names), 3);
    assert.equal(added, 2);
    assert.equal(notDrawn, 3);
    assert.equal(graph.nodes.size, 3);
    assert.equal(graph.edges.length, 2);
    assert.equal(graph.nodes.get(id("fixture-wide"))?.notDrawn, 3);
    assert.equal(graph.nodes.get(id("fixture-wide"))?.read, true);
  });

  it("still links to a package it already has when the graph is full", () => {
    const base = addRead(emptyGraph(), read("fixture-web", ["fixture-router", "fixture-utils"]), 3).graph;
    const { graph, notDrawn } = addRead(base, read("fixture-router", ["fixture-utils", "fixture-new"]), 3);
    assert.equal(notDrawn, 1);
    assert.equal(graph.edges.some((edge) => edge.from === id("fixture-router") && edge.to === id("fixture-utils")), true);
    assert.equal(graph.nodes.has(id("fixture-new")), false);
  });

  it("holds MAX_NODES nodes at most by default", () => {
    const names = Array.from({ length: MAX_NODES + 50 }, (_, index) => `fixture-many-${index}`);
    const { graph, notDrawn } = addRead(emptyGraph(), read("fixture-many", names));
    assert.equal(graph.nodes.size, MAX_NODES);
    assert.equal(notDrawn, 51);
  });
});

describe("layoutLayers", () => {
  const web = build(
    read("fixture-web", ["fixture-router", "fixture-templates", "fixture-utils"]),
    read("fixture-router", ["fixture-utils"]),
  );

  const columnOf = (graph: DependencyGraph, name: string): number | undefined =>
    layoutLayers(graph).nodes.find((node) => node.id === id(name))?.column;

  it("steps each column one node-width and a gap to the right", () => {
    const layout = layoutLayers(web);
    const pitchX = DEFAULT_METRICS.nodeWidth + DEFAULT_METRICS.columnGap;
    for (const node of layout.nodes) assert.equal(node.x, DEFAULT_METRICS.padding + node.column * pitchX);
  });

  it("puts a package a column to the right of every package that lists it", () => {
    const layout = layoutLayers(web);
    const column = new Map(layout.nodes.map((node) => [node.id, node.column] as const));
    for (const edge of web.edges) assert.ok((column.get(edge.to) as number) > (column.get(edge.from) as number), `${edge.from} -> ${edge.to}`);
  });

  it("puts a shared package after the siblings that need it, even when the root lists it too", () => {
    assert.deepEqual(
      ["fixture-web", "fixture-router", "fixture-templates", "fixture-utils"].map((name) => columnOf(web, name)),
      [0, 1, 1, 2],
    );
    assert.equal(web.nodes.get(id("fixture-utils"))?.depth, 1); // the fewest steps is still kept for what it means
  });

  it("puts the end of a diamond after both of its sides", () => {
    const diamond = build(read("fixture-web", ["fixture-a", "fixture-b"]), read("fixture-a", ["fixture-c"]), read("fixture-b", ["fixture-c"]));
    assert.equal(columnOf(diamond, "fixture-c"), 2);
  });

  it("follows the longest chain", () => {
    const chain = build(
      read("fixture-web", ["fixture-a", "fixture-d"]),
      read("fixture-a", ["fixture-b"]),
      read("fixture-b", ["fixture-c"]),
      read("fixture-c", ["fixture-d"]),
    );
    assert.deepEqual(["fixture-a", "fixture-b", "fixture-c", "fixture-d"].map((name) => columnOf(chain, name)), [1, 2, 3, 4]);
  });

  it("leaves the line that closes a cycle out of the count, so a cycle does not run away to the right", () => {
    const cycle = build(read("fixture-cycle-a", ["fixture-cycle-b"]), read("fixture-cycle-b", ["fixture-cycle-a"]));
    assert.deepEqual(["fixture-cycle-a", "fixture-cycle-b"].map((name) => columnOf(cycle, name)), [0, 1]);
    const selfish = build(read("fixture-self", ["fixture-self", "fixture-utils"]));
    assert.deepEqual(["fixture-self", "fixture-utils"].map((name) => columnOf(selfish, name)), [0, 1]);
  });

  it("never puts two nodes in the same place", () => {
    const layout = layoutLayers(web);
    assert.equal(new Set(layout.nodes.map((node) => `${node.x},${node.y}`)).size, layout.nodes.length);
  });

  it("centres a short column against the tallest one", () => {
    const layout = layoutLayers(web);
    const pitch = DEFAULT_METRICS.nodeHeight + DEFAULT_METRICS.rowGap;
    const root = layout.nodes.find((node) => node.id === id("fixture-web"));
    assert.equal(root?.y, DEFAULT_METRICS.padding + pitch / 2);
    const utils = layout.nodes.find((node) => node.id === id("fixture-utils"));
    assert.equal(utils?.y, DEFAULT_METRICS.padding + pitch / 2);
  });

  it("sizes the drawing to hold every node with the padding around it", () => {
    const layout = layoutLayers(web);
    const metrics = DEFAULT_METRICS;
    assert.equal(layout.width, 2 * metrics.padding + 3 * metrics.nodeWidth + 2 * metrics.columnGap);
    assert.equal(layout.height, 2 * metrics.padding + 2 * metrics.nodeHeight + metrics.rowGap);
    for (const node of layout.nodes) {
      assert.ok(node.x + metrics.nodeWidth + metrics.padding <= layout.width);
      assert.ok(node.y + metrics.nodeHeight + metrics.padding <= layout.height);
    }
  });

  it("orders a column by where its dependents sit, not only the order the nodes were met", () => {
    const graph = build(
      read("fixture-web", ["fixture-a", "fixture-b"]),
      read("fixture-b", ["fixture-y"]),
      read("fixture-a", ["fixture-z"]),
    );
    const layout = layoutLayers(graph);
    const row = (name: string): number | undefined => layout.nodes.find((node) => node.id === id(name))?.row;
    assert.equal(row("fixture-z"), 0);
    assert.equal(row("fixture-y"), 1);
  });

  it("gives a wide package one tall column", () => {
    const names = Array.from({ length: 40 }, (_, index) => `fixture-wide-${index}`);
    const layout = layoutLayers(addRead(emptyGraph(), read("fixture-wide", names)).graph);
    const pitch = DEFAULT_METRICS.nodeHeight + DEFAULT_METRICS.rowGap;
    assert.equal(layout.height, 2 * DEFAULT_METRICS.padding + 40 * pitch - DEFAULT_METRICS.rowGap);
    assert.equal(layout.nodes.filter((node) => node.column === 1).length, 40);
  });

  it("is the same every time for the same graph", () => {
    assert.deepEqual(layoutLayers(web), layoutLayers(web));
  });

  it("lays out an empty graph as nothing", () => {
    assert.equal(layoutLayers(emptyGraph()).nodes.length, 0);
  });
});

describe("edgePath", () => {
  it("leaves the right side of one node and enters the left side of the next", () => {
    const from = { id: "a", x: 16, y: 16, column: 0, row: 0 };
    const to = { id: "b", x: 268, y: 16, column: 1, row: 0 };
    assert.equal(edgePath(from, to), "M192 31C230 31 230 31 268 31");
  });

  it("swings out and back for an edge that does not go to the right", () => {
    const from = { id: "a", x: 268, y: 16, column: 1, row: 0 };
    const to = { id: "b", x: 16, y: 56, column: 0, row: 1 };
    const path = edgePath(from, to);
    assert.ok(path.startsWith("M444 31C"));
    assert.ok(path.endsWith(" 16 71"));
  });
});

describe("stepFrom", () => {
  const graph = build(
    read("fixture-web", ["fixture-router", "fixture-templates"]),
    read("fixture-router", ["fixture-utils"]),
  );
  const layout = layoutLayers(graph);

  it("goes right to a dependency and left to a dependent", () => {
    assert.equal(stepFrom(graph, layout, id("fixture-router"), "right"), id("fixture-utils"));
    assert.equal(stepFrom(graph, layout, id("fixture-utils"), "left"), id("fixture-router"));
  });

  it("goes up and down within a column", () => {
    const column = layout.nodes.filter((node) => node.column === 1).sort((a, b) => a.row - b.row);
    assert.equal(stepFrom(graph, layout, column[0]?.id as string, "down"), column[1]?.id);
    assert.equal(stepFrom(graph, layout, column[1]?.id as string, "up"), column[0]?.id);
  });

  it("does nothing at an edge, rather than jumping somewhere unrelated", () => {
    assert.equal(stepFrom(graph, layout, id("fixture-web"), "left"), null);
    assert.equal(stepFrom(graph, layout, id("fixture-web"), "up"), null);
    assert.equal(stepFrom(graph, layout, id("fixture-templates"), "right"), null);
    assert.equal(stepFrom(graph, layout, id("fixture-utils"), "right"), null);
  });

  it("goes right to the nearest column that holds a dependency, not past it", () => {
    const shortcut = build(read("fixture-web", ["fixture-router", "fixture-utils"]), read("fixture-router", ["fixture-utils"]));
    const shortcutLayout = layoutLayers(shortcut);
    assert.equal(stepFrom(shortcut, shortcutLayout, id("fixture-web"), "right"), id("fixture-router"));
    assert.equal(stepFrom(shortcut, shortcutLayout, id("fixture-router"), "right"), id("fixture-utils"));
    assert.equal(stepFrom(shortcut, shortcutLayout, id("fixture-utils"), "left"), id("fixture-router"));
  });

  it("does not follow the line that closes a cycle", () => {
    const cycle = build(read("fixture-cycle-a", ["fixture-cycle-b"]), read("fixture-cycle-b", ["fixture-cycle-a"]));
    const cycleLayout = layoutLayers(cycle);
    assert.equal(stepFrom(cycle, cycleLayout, id("fixture-cycle-b"), "right"), null);
    assert.equal(stepFrom(cycle, cycleLayout, id("fixture-cycle-a"), "left"), null);
  });

  it("does nothing for a node it does not know", () => {
    assert.equal(stepFrom(graph, layout, "pypi:missing", "right"), null);
  });

  it("goes right to the dependency level with it when there are several", () => {
    const wide = build(read("fixture-web", ["fixture-a", "fixture-b", "fixture-c"]));
    const wideLayout = layoutLayers(wide);
    const answer = stepFrom(wide, wideLayout, id("fixture-web"), "right");
    assert.equal(answer, id("fixture-b"));
  });

  it("only follows edges, not nodes that happen to sit in the next column", () => {
    const apart = build(read("fixture-web", ["fixture-router", "fixture-templates"]), read("fixture-router", ["fixture-utils"]));
    const apartLayout = layoutLayers(apart);
    assert.equal(stepFrom(apart, apartLayout, id("fixture-templates"), "right"), null);
  });
});

describe("dependencyOutline", () => {
  it("lists each package with its dependencies indented below it, in drawing order", () => {
    const graph = build(
      read("fixture-web", [dep("fixture-router", ">=1.0"), "fixture-templates"]),
      read("fixture-router", [dep("fixture-utils", "<2")]),
    );
    const rows = dependencyOutline(graph, layoutLayers(graph));
    assert.deepEqual(
      rows.map((row) => [row.id, row.level, row.repeated, row.requirement]),
      [
        [id("fixture-web"), 0, false, null],
        [id("fixture-router"), 1, false, ">=1.0"],
        [id("fixture-utils"), 2, false, "<2"],
        [id("fixture-templates"), 1, false, null],
      ],
    );
  });

  it("lists a shared package where it first appears and marks later mentions as repeated, without its dependencies", () => {
    const graph = build(
      read("fixture-web", ["fixture-router", "fixture-templates"]),
      read("fixture-router", ["fixture-utils"]),
      read("fixture-templates", ["fixture-utils"]),
      read("fixture-utils", ["fixture-markup"]),
    );
    const rows = dependencyOutline(graph, layoutLayers(graph));
    const utils = rows.filter((row) => row.id === id("fixture-utils"));
    assert.deepEqual(utils.map((row) => row.repeated), [false, true]);
    assert.equal(rows.filter((row) => row.id === id("fixture-markup")).length, 1);
  });

  it("ends at a cycle", () => {
    const graph = build(read("fixture-cycle-a", ["fixture-cycle-b"]), read("fixture-cycle-b", ["fixture-cycle-a"]));
    const rows = dependencyOutline(graph, layoutLayers(graph));
    assert.deepEqual(rows.map((row) => [row.id, row.repeated]), [
      [id("fixture-cycle-a"), false],
      [id("fixture-cycle-b"), false],
      [id("fixture-cycle-a"), true],
    ]);
  });

  it("is empty for an empty graph", () => {
    assert.deepEqual(dependencyOutline(emptyGraph(), layoutLayers(emptyGraph())), []);
  });
});

describe("nodeStatus", () => {
  it("says read, unread or unreadable", () => {
    const graph = build(read("fixture-web", ["fixture-router", dep("fixture-direct", null, false)]));
    assert.equal(nodeStatus(graph.nodes.get(id("fixture-web")) as never), "read");
    assert.equal(nodeStatus(graph.nodes.get(id("fixture-router")) as never), "unread");
    assert.equal(nodeStatus(graph.nodes.get(id("fixture-direct")) as never), "unreadable");
  });
});
