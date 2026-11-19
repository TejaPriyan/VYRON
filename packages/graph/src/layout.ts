/**
 * A deterministic, cluster-aware force layout.
 *
 * The same nodes, links and groups always give the same positions: starting points come from
 * `seedPosition` (a hash of the id), the loop has a fixed number of steps, nodes are processed in id
 * order and there is no randomness or clock. That is what lets a shared link, a reload and a second
 * neighbourhood load all show the same map.
 *
 * Each group (a cluster) gets an anchor. Anchors are spread on a sunflower spiral with the largest
 * group in the middle, then pushed apart until their discs clear each other. Members are pulled to
 * their anchor, connected nodes pull on each other, and every pair of nodes repels, so clusters read as
 * separate territories while the links between them stay short enough to follow.
 *
 * Units are relative: `linkLength` is the resting length of a link inside a group (default 1).
 * Callers scale the result to the screen. O(n^2) per step, so it is meant for the few hundred nodes
 * one API response can carry (docs/API.md), not for a whole large graph.
 */
import { seedPosition } from "./layout-seed.ts";
import type { Point } from "./lod.ts";

export interface LayoutNode {
  readonly id: string;
  /** The cluster the node belongs to. */
  readonly group: string;
}

export interface LayoutLink {
  readonly source: string;
  readonly target: string;
}

export interface LayoutOptions {
  /** Number of steps (default 360). More is calmer, not different. */
  readonly iterations?: number;
  /** Resting length of a link inside one group (default 1). Links between groups rest at 2.2 times this. */
  readonly linkLength?: number;
  /** Half the clearance kept around each node, as a fraction of `linkLength` (default 0.3). */
  readonly nodeRadius?: number;
}

export interface Layout {
  readonly positions: ReadonlyMap<string, Point>;
  /** Where each group is pulled to, in the same frame as `positions`. */
  readonly anchors: ReadonlyMap<string, Point>;
}

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
const compareText = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

interface Body {
  readonly id: string;
  readonly group: string;
  x: number;
  y: number;
}

export function computeLayout(
  nodes: ReadonlyArray<LayoutNode>,
  links: ReadonlyArray<LayoutLink>,
  options: LayoutOptions = {},
): Layout {
  const iterations = Math.max(1, Math.floor(options.iterations ?? 360));
  const unit = options.linkLength ?? 1;
  if (!(unit > 0) || !Number.isFinite(unit)) throw new RangeError("linkLength must be a positive finite number");
  const clearance = 2 * (options.nodeRadius ?? 0.3) * unit;

  // One body per distinct id, in id order, so nothing depends on the order the caller passed.
  const unique = new Map<string, LayoutNode>();
  for (const node of nodes) if (!unique.has(node.id)) unique.set(node.id, node);
  const ordered = [...unique.values()].sort((a, b) => compareText(a.id, b.id));

  const members = new Map<string, number>();
  for (const node of ordered) members.set(node.group, (members.get(node.group) ?? 0) + 1);
  const groups = [...members.keys()].sort((a, b) => (members.get(b) ?? 0) - (members.get(a) ?? 0) || compareText(a, b));
  const discRadius = (group: string): number => unit * (0.5 + 0.5 * Math.sqrt(members.get(group) ?? 1));

  const anchors = placeAnchors(groups, discRadius, unit);

  const bodies: Body[] = ordered.map((node) => {
    const anchor = anchors.get(node.group) ?? { x: 0, y: 0 };
    const seed = seedPosition(node.id);
    const spread = discRadius(node.group) * 0.8;
    return { id: node.id, group: node.group, x: anchor.x + seed.x * spread, y: anchor.y + seed.y * spread };
  });
  const index = new Map<string, number>(bodies.map((body, i) => [body.id, i]));

  // Undirected, de-duplicated, no self-loops, no unknown ends.
  const pairs = new Map<string, readonly [number, number]>();
  for (const link of links) {
    const a = index.get(link.source);
    const b = index.get(link.target);
    if (a === undefined || b === undefined || a === b) continue;
    const key = a < b ? `${a}:${b}` : `${b}:${a}`;
    if (!pairs.has(key)) pairs.set(key, a < b ? [a, b] : [b, a]);
  }
  const edges = [...pairs.entries()].sort(([x], [y]) => compareText(x, y)).map(([, pair]) => pair);

  const n = bodies.length;
  const fx = new Float64Array(n);
  const fy = new Float64Array(n);
  for (let step = 0; step < iterations; step += 1) {
    const alpha = Math.pow(1 - step / iterations, 1.5);
    fx.fill(0);
    fy.fill(0);

    for (let i = 0; i < n; i += 1) {
      const a = bodies[i];
      if (!a) continue;
      for (let j = i + 1; j < n; j += 1) {
        const b = bodies[j];
        if (!b) continue;
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let distance = Math.hypot(dx, dy);
        if (distance < 1e-6) {
          // Identical points: separate along a fixed direction that depends only on the pair.
          const angle = (i * 31 + j * 17) * GOLDEN_ANGLE;
          dx = Math.cos(angle) * 1e-3;
          dy = Math.sin(angle) * 1e-3;
          distance = 1e-3;
        }
        const push = (0.55 * unit * unit) / Math.max(distance * distance, 0.04 * unit * unit);
        const ux = dx / distance;
        const uy = dy / distance;
        fx[i] = (fx[i] ?? 0) - ux * push;
        fy[i] = (fy[i] ?? 0) - uy * push;
        fx[j] = (fx[j] ?? 0) + ux * push;
        fy[j] = (fy[j] ?? 0) + uy * push;
      }
    }

    for (const [i, j] of edges) {
      const a = bodies[i];
      const b = bodies[j];
      if (!a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const distance = Math.max(Math.hypot(dx, dy), 1e-6);
      const sameGroup = a.group === b.group;
      const rest = sameGroup ? unit : 2.2 * unit;
      const pull = (sameGroup ? 0.34 : 0.14) * (distance - rest);
      const ux = dx / distance;
      const uy = dy / distance;
      fx[i] = (fx[i] ?? 0) + ux * pull;
      fy[i] = (fy[i] ?? 0) + uy * pull;
      fx[j] = (fx[j] ?? 0) - ux * pull;
      fy[j] = (fy[j] ?? 0) - uy * pull;
    }

    for (let i = 0; i < n; i += 1) {
      const body = bodies[i];
      const anchor = body ? anchors.get(body.group) : undefined;
      if (!body || !anchor) continue;
      fx[i] = (fx[i] ?? 0) + 0.2 * (anchor.x - body.x);
      fy[i] = (fy[i] ?? 0) + 0.2 * (anchor.y - body.y);
    }

    const cap = 0.5 * unit;
    for (let i = 0; i < n; i += 1) {
      const body = bodies[i];
      if (!body) continue;
      let mx = (fx[i] ?? 0) * 0.5 * alpha;
      let my = (fy[i] ?? 0) * 0.5 * alpha;
      const length = Math.hypot(mx, my);
      if (length > cap) {
        mx = (mx / length) * cap;
        my = (my / length) * cap;
      }
      body.x += mx;
      body.y += my;
    }

    separate(bodies, clearance);
  }
  for (let pass = 0; pass < 12; pass += 1) separate(bodies, clearance); // the loop ends with a clean frame

  // Centre on the middle of the nodes so every caller starts from the origin.
  let cx = 0;
  let cy = 0;
  for (const body of bodies) {
    cx += body.x;
    cy += body.y;
  }
  if (n > 0) {
    cx /= n;
    cy /= n;
  }
  const positions = new Map<string, Point>(bodies.map((b) => [b.id, { x: b.x - cx, y: b.y - cy }]));
  const centredAnchors = new Map<string, Point>([...anchors].map(([g, p]) => [g, { x: p.x - cx, y: p.y - cy }]));
  return { positions, anchors: centredAnchors };
}

/** Sunflower spiral, biggest group first, then relaxed until no two group discs overlap. */
function placeAnchors(
  groups: ReadonlyArray<string>,
  discRadius: (group: string) => number,
  unit: number,
): Map<string, Point> {
  const points = groups.map((group, i) => {
    const radius = unit * 2.6 * Math.sqrt(i);
    const angle = i * GOLDEN_ANGLE;
    return { group, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
  });
  const gap = 0.7 * unit;
  for (let pass = 0; pass < 80; pass += 1) {
    let moved = false;
    for (let i = 0; i < points.length; i += 1) {
      for (let j = i + 1; j < points.length; j += 1) {
        const a = points[i];
        const b = points[j];
        if (!a || !b) continue;
        const need = discRadius(a.group) + discRadius(b.group) + gap;
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let distance = Math.hypot(dx, dy);
        if (distance >= need) continue;
        if (distance < 1e-9) {
          dx = 1;
          dy = 0;
          distance = 1;
        }
        const shift = (need - distance) / 2;
        const ux = dx / distance;
        const uy = dy / distance;
        // The first group (the largest) stays put; the other moves the whole way.
        if (i === 0) {
          b.x += ux * shift * 2;
          b.y += uy * shift * 2;
        } else {
          a.x -= ux * shift;
          a.y -= uy * shift;
          b.x += ux * shift;
          b.y += uy * shift;
        }
        moved = true;
      }
    }
    if (!moved) break;
  }
  return new Map(points.map((p) => [p.group, { x: p.x, y: p.y }]));
}

/** Push apart any two bodies closer than `clearance`. */
function separate(bodies: Body[], clearance: number): void {
  for (let i = 0; i < bodies.length; i += 1) {
    const a = bodies[i];
    if (!a) continue;
    for (let j = i + 1; j < bodies.length; j += 1) {
      const b = bodies[j];
      if (!b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const distance = Math.hypot(dx, dy);
      if (distance >= clearance) continue;
      if (distance < 1e-9) {
        a.x -= clearance / 2;
        b.x += clearance / 2;
        continue;
      }
      const shift = (clearance - distance) / 2;
      a.x -= (dx / distance) * shift;
      a.y -= (dy / distance) * shift;
      b.x += (dx / distance) * shift;
      b.y += (dy / distance) * shift;
    }
  }
}
