/**
 * A package's dependency graph as it is read, one package at a time: what depends on what, in which column to draw
 * it, which node a key press moves to, and the same graph as a text outline.
 *
 * Pure functions with no DOM. Nothing here reads a registry or invents an edge: a node exists because some package's own
 * metadata lists it, and an edge exists because that package lists it. A dependency that has not been opened has no
 * edges of its own yet, which is not the same as having none.
 */

export type PackageRegistry = "pypi" | "npm";

/** The most nodes the graph holds. The API lists up to 500 dependencies for one package. */
export const MAX_NODES = 200;

/** What the graph needs of a dependency: structurally the API's `PackageDependency`. */
export interface DependencyRef {
  readonly name: string;
  readonly requirement: string | null;
  readonly readable: boolean;
}

/** What the graph needs of one package read: structurally the API's `PackageReport`. */
export interface PackageRead {
  readonly registry: PackageRegistry;
  readonly name: string;
  readonly version: string;
  readonly dependencies: ReadonlyArray<DependencyRef>;
}

export interface DepNode {
  readonly id: string;
  readonly registry: PackageRegistry;
  readonly name: string;
  /** Fewest dependency steps from the root. */
  readonly depth: number;
  /** Its own dependencies have been read, so its edges are all there. */
  readonly read: boolean;
  /** Some package lists it in a form the registry can serve by name. The root is always readable. */
  readonly readable: boolean;
  readonly version: string | null;
  /** Dependencies it lists that were not added because the graph was full. */
  readonly notDrawn: number;
}

export interface DepEdge {
  readonly from: string;
  readonly to: string;
  readonly requirement: string | null;
  readonly readable: boolean;
}

export interface DependencyGraph {
  readonly root: string | null;
  /** In the order they were first met, which is the order ties are drawn in. */
  readonly nodes: ReadonlyMap<string, DepNode>;
  readonly edges: ReadonlyArray<DepEdge>;
}

export const packageId = (registry: PackageRegistry, name: string): string => `${registry}:${name}`;

export const emptyGraph = (): DependencyGraph => ({ root: null, nodes: new Map(), edges: [] });

export interface AddResult {
  readonly graph: DependencyGraph;
  /** New nodes drawn. */
  readonly added: number;
  /** The dependencies left out because the graph is full (the package's node is still marked read). */
  readonly notDrawn: number;
}

function withDepths(nodes: Map<string, DepNode>, edges: ReadonlyArray<DepEdge>, root: string): Map<string, DepNode> {
  const out = new Map<string, string[]>();
  for (const edge of edges) out.set(edge.from, [...(out.get(edge.from) ?? []), edge.to]);
  const depth = new Map<string, number>([[root, 0]]);
  const queue = [root];
  for (let index = 0; index < queue.length; index += 1) {
    const from = queue[index] as string;
    for (const to of out.get(from) ?? []) {
      if (!depth.has(to)) {
        depth.set(to, (depth.get(from) as number) + 1);
        queue.push(to);
      }
    }
  }
  const result = new Map<string, DepNode>();
  for (const [id, node] of nodes) result.set(id, { ...node, depth: depth.get(id) ?? node.depth });
  return result;
}

/**
 * Add one package read. The first read becomes the root. Reading a package a second time replaces its edges, so
 * reading again after a failure or a newer answer never doubles anything, and a dependency the newer answer no longer
 * lists leaves with it. A package that is not yet in the graph is added, so a read of an unlisted package never
 * silently vanishes.
 */
export function addRead(graph: DependencyGraph, read: PackageRead, max: number = MAX_NODES): AddResult {
  const id = packageId(read.registry, read.name);
  const root = graph.root ?? id;
  const nodes = new Map(graph.nodes);
  const existing = nodes.get(id);
  nodes.set(id, {
    id,
    registry: read.registry,
    name: read.name,
    depth: existing?.depth ?? 0,
    read: true,
    readable: true,
    version: read.version,
    notDrawn: 0,
  });

  const listed = new Map<string, DependencyRef>();
  for (const dependency of read.dependencies) if (!listed.has(dependency.name)) listed.set(dependency.name, dependency);
  const fresh = [...listed.keys()].filter((name) => !nodes.has(packageId(read.registry, name)));
  const room = Math.max(0, max - nodes.size);
  const taken = new Set(fresh.slice(0, room));
  const notDrawn = fresh.length - taken.size;

  const edges = graph.edges.filter((edge) => edge.from !== id);
  for (const [name, dependency] of listed) {
    const to = packageId(read.registry, name);
    if (!nodes.has(to) && !taken.has(name)) continue;
    edges.push({ from: id, to, requirement: dependency.requirement, readable: dependency.readable });
    const known = nodes.get(to);
    nodes.set(to, {
      id: to,
      registry: read.registry,
      name,
      depth: known?.depth ?? 0,
      read: known?.read ?? false,
      readable: (known?.readable ?? false) || dependency.readable,
      version: known?.version ?? null,
      notDrawn: known?.notDrawn ?? 0,
    });
  }
  const self = nodes.get(id) as DepNode;
  nodes.set(id, { ...self, notDrawn });
  const kept = reachableFrom(edges, [root, id]);
  const stillHere = new Map([...nodes].filter(([key]) => kept.has(key)));
  const stillLinked = edges.filter((edge) => kept.has(edge.from) && kept.has(edge.to));
  const settled = withDepths(stillHere, stillLinked, root);
  return { graph: { root, nodes: settled, edges: stillLinked }, added: taken.size, notDrawn };
}

/** Every node that can be reached from the starting ones, so a dependency dropped by a newer read leaves no orphan behind. */
function reachableFrom(edges: ReadonlyArray<DepEdge>, starts: ReadonlyArray<string>): Set<string> {
  const out = new Map<string, string[]>();
  for (const edge of edges) out.set(edge.from, [...(out.get(edge.from) ?? []), edge.to]);
  const seen = new Set(starts);
  const queue = [...starts];
  for (let index = 0; index < queue.length; index += 1) {
    for (const to of out.get(queue[index] as string) ?? []) {
      if (!seen.has(to)) {
        seen.add(to);
        queue.push(to);
      }
    }
  }
  return seen;
}

// ---- layout: one column per step from the root --------------------------------------------------

export interface LayoutMetrics {
  readonly nodeWidth: number;
  readonly nodeHeight: number;
  readonly columnGap: number;
  readonly rowGap: number;
  readonly padding: number;
}

export const DEFAULT_METRICS: LayoutMetrics = { nodeWidth: 176, nodeHeight: 30, columnGap: 76, rowGap: 10, padding: 16 };

export interface PlacedNode {
  readonly id: string;
  readonly x: number;
  readonly y: number;
  readonly column: number;
  readonly row: number;
}

export interface LayerLayout {
  readonly metrics: LayoutMetrics;
  readonly nodes: ReadonlyArray<PlacedNode>;
  readonly width: number;
  readonly height: number;
}

/**
 * The column of every node: the longest chain of dependencies that leads to it from the root, so every line runs
 * left to right. (The fewest steps, `DepNode.depth`, would put a package that is both a direct dependency and
 * something a sibling needs in the same column as that sibling, and the line between them would loop back.) An edge
 * that closes a cycle is left out of the count, so a cycle cannot push its own members ever further right.
 */
function columnsOf(graph: DependencyGraph): Map<string, number> {
  const out = new Map<string, string[]>();
  for (const edge of graph.edges) out.set(edge.from, [...(out.get(edge.from) ?? []), edge.to]);
  const state = new Map<string, "open" | "done">();
  const closing = new Set<string>();
  const finished: string[] = [];
  const visit = (id: string): void => {
    state.set(id, "open");
    for (const to of out.get(id) ?? []) {
      const seen = state.get(to);
      if (seen === "open") closing.add(`${id}\u0000${to}`);
      else if (seen === undefined) visit(to);
    }
    state.set(id, "done");
    finished.push(id);
  };
  const starts = graph.root === null ? [...graph.nodes.keys()] : [graph.root, ...graph.nodes.keys()];
  for (const id of starts) if (!state.has(id)) visit(id);

  const column = new Map<string, number>([...graph.nodes.keys()].map((id) => [id, 0] as const));
  for (const id of [...finished].reverse()) {
    for (const to of out.get(id) ?? []) {
      if (closing.has(`${id}\u0000${to}`)) continue;
      column.set(to, Math.max(column.get(to) as number, (column.get(id) as number) + 1));
    }
  }
  return column;
}

/**
 * Columns run left to right by longest chain from the root. Inside a column a node sits at the mean row of the
 * nodes that depend on it in the columns before (fewer crossings), ties in the order they were met. Each column is
 * centred against the tallest, so the root sits level with the middle of what it depends on.
 */
export function layoutLayers(graph: DependencyGraph, metrics: LayoutMetrics = DEFAULT_METRICS): LayerLayout {
  const order = new Map<string, number>();
  [...graph.nodes.keys()].forEach((id, index) => order.set(id, index));
  const column = columnsOf(graph);
  const columns = new Map<number, string[]>();
  for (const id of graph.nodes.keys()) {
    const at = column.get(id) as number;
    columns.set(at, [...(columns.get(at) ?? []), id]);
  }
  const depths = [...columns.keys()].sort((a, b) => a - b);

  const rowOf = new Map<string, number>();
  for (const depth of depths) {
    const ids = columns.get(depth) as string[];
    const key = (id: string): number => {
      const parents = graph.edges.filter((edge) => edge.to === id && rowOf.has(edge.from));
      if (parents.length === 0) return Number.MAX_SAFE_INTEGER;
      return parents.reduce((sum, edge) => sum + (rowOf.get(edge.from) as number), 0) / parents.length;
    };
    ids.sort((a, b) => key(a) - key(b) || (order.get(a) as number) - (order.get(b) as number));
    ids.forEach((id, row) => rowOf.set(id, row));
  }

  const pitch = metrics.nodeHeight + metrics.rowGap;
  const tallest = Math.max(1, ...depths.map((depth) => (columns.get(depth) as string[]).length));
  const placed: PlacedNode[] = [];
  for (const depth of depths) {
    const ids = columns.get(depth) as string[];
    const offset = ((tallest - ids.length) * pitch) / 2;
    ids.forEach((id, row) =>
      placed.push({
        id,
        x: metrics.padding + depth * (metrics.nodeWidth + metrics.columnGap),
        y: metrics.padding + offset + row * pitch,
        column: depth,
        row,
      }),
    );
  }
  const lastColumn = depths.length === 0 ? 0 : (depths[depths.length - 1] as number) + 1;
  return {
    metrics,
    nodes: placed,
    width: 2 * metrics.padding + lastColumn * metrics.nodeWidth + Math.max(0, lastColumn - 1) * metrics.columnGap,
    height: 2 * metrics.padding + Math.max(0, tallest * pitch - metrics.rowGap),
  };
}

/** The curve for one edge: out of the right side of `from`, into the left side of `to` (looping back if `to` is not to the right). */
export function edgePath(from: PlacedNode, to: PlacedNode, metrics: LayoutMetrics = DEFAULT_METRICS): string {
  const x1 = from.x + metrics.nodeWidth;
  const y1 = from.y + metrics.nodeHeight / 2;
  const x2 = to.x;
  const y2 = to.y + metrics.nodeHeight / 2;
  const reach = x2 > x1 ? Math.max(24, (x2 - x1) / 2) : metrics.columnGap;
  return `M${round(x1)} ${round(y1)}C${round(x1 + reach)} ${round(y1)} ${round(x2 - reach)} ${round(y2)} ${round(x2)} ${round(y2)}`;
}

const round = (value: number): number => Math.round(value * 10) / 10;

// ---- keyboard: which node an arrow key goes to ------------------------------------------------------

export type StepDirection = "up" | "down" | "left" | "right";

/**
 * Up and down are the neighbours in the same column. Right is a dependency in a later column and left is a dependent in
 * an earlier one: the nearest column first, then whichever is level with the current node. `null` when there is no such
 * node, so a key press at an edge does nothing rather than jumping somewhere unrelated. A line that closes a cycle
 * points back the way the arrows do not go, so it is reached from the details and the outline instead.
 */
export function stepFrom(graph: DependencyGraph, layout: LayerLayout, id: string, direction: StepDirection): string | null {
  const here = layout.nodes.find((node) => node.id === id);
  if (!here) return null;
  if (direction === "up" || direction === "down") {
    const wanted = here.row + (direction === "up" ? -1 : 1);
    return layout.nodes.find((node) => node.column === here.column && node.row === wanted)?.id ?? null;
  }
  const linked = new Set<string>();
  for (const edge of graph.edges) {
    if (direction === "right" && edge.from === id) linked.add(edge.to);
    if (direction === "left" && edge.to === id) linked.add(edge.from);
  }
  const sign = direction === "right" ? 1 : -1;
  let best: PlacedNode | null = null;
  for (const node of layout.nodes) {
    if (!linked.has(node.id) || (node.column - here.column) * sign <= 0) continue;
    const nearer =
      best === null ||
      Math.abs(node.column - here.column) < Math.abs(best.column - here.column) ||
      (node.column === best.column && Math.abs(node.y - here.y) < Math.abs(best.y - here.y));
    if (nearer) best = node;
  }
  return best?.id ?? null;
}

// ---- the same graph as text --------------------------------------------------------------------

export interface OutlineRow {
  readonly id: string;
  readonly level: number;
  /** It was listed above already, so what it depends on is not listed again here. */
  readonly repeated: boolean;
  readonly requirement: string | null;
}

/** The graph as an indented list in drawing order: each package once with its dependencies below it. */
export function dependencyOutline(graph: DependencyGraph, layout: LayerLayout): ReadonlyArray<OutlineRow> {
  if (graph.root === null) return [];
  const rowOf = new Map(layout.nodes.map((node) => [node.id, node.row] as const));
  const children = (id: string): DepEdge[] =>
    graph.edges.filter((edge) => edge.from === id).sort((a, b) => (rowOf.get(a.to) ?? 0) - (rowOf.get(b.to) ?? 0));
  const rows: OutlineRow[] = [];
  const seen = new Set<string>();
  const walk = (id: string, level: number, requirement: string | null): void => {
    const repeated = seen.has(id);
    rows.push({ id, level, repeated, requirement });
    if (repeated) return;
    seen.add(id);
    for (const edge of children(id)) walk(edge.to, level + 1, edge.requirement);
  };
  walk(graph.root, 0, null);
  return rows;
}

/** What each node is, in words: never carried by an outline's colour or a border alone. */
export type NodeStatus = "read" | "unread" | "unreadable";

export function nodeStatus(node: DepNode): NodeStatus {
  if (node.read) return "read";
  return node.readable ? "unread" : "unreadable";
}
