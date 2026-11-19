/**
 * Territories: the outline drawn around a cluster's members.
 *
 * A territory is the convex hull of a circle around each member, so one member gives a disc, two
 * give a capsule and many give a rounded blob, with no special cases. Pure geometry, no DOM.
 */
import type { Point } from "./lod.ts";

function cross(o: Point, a: Point, b: Point): number {
  return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

/**
 * The convex hull of `points` (Andrew's monotone chain) without repeated points or points that sit
 * on an edge. Fewer than three distinct points are returned as they are, sorted.
 */
export function convexHull(points: ReadonlyArray<Point>): Point[] {
  const sorted = [...points]
    .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y))
    .sort((a, b) => a.x - b.x || a.y - b.y)
    .filter((p, i, all) => i === 0 || p.x !== all[i - 1]?.x || p.y !== all[i - 1]?.y);
  if (sorted.length < 3) return sorted;
  const build = (input: ReadonlyArray<Point>): Point[] => {
    const chain: Point[] = [];
    for (const p of input) {
      while (chain.length >= 2) {
        const a = chain[chain.length - 2];
        const b = chain[chain.length - 1];
        if (a && b && cross(a, b, p) <= 0) chain.pop();
        else break;
      }
      chain.push(p);
    }
    chain.pop();
    return chain;
  };
  return [...build(sorted), ...build([...sorted].reverse())];
}

/** The hull of a circle of `radius` around every centre, each circle sampled at `samples` points. */
export function paddedHull(centres: ReadonlyArray<Point>, radius: number, samples = 20): Point[] {
  if (!(radius > 0) || samples < 3) throw new RangeError("radius must be above 0 and samples at least 3");
  const ring: Point[] = [];
  for (const c of centres) {
    for (let i = 0; i < samples; i += 1) {
      const angle = (i / samples) * 2 * Math.PI;
      ring.push({ x: c.x + Math.cos(angle) * radius, y: c.y + Math.sin(angle) * radius });
    }
  }
  return convexHull(ring);
}

/** A closed SVG path through `points`; an empty string for no points. */
export function polygonPath(points: ReadonlyArray<Point>): string {
  if (points.length === 0) return "";
  const fixed = (n: number): string => String(Math.round(n * 10) / 10);
  return `${points.map((p, i) => `${i === 0 ? "M" : "L"}${fixed(p.x)} ${fixed(p.y)}`).join("")}Z`;
}

/** The mean of the points, or null for none. */
export function meanPoint(points: ReadonlyArray<Point>): Point | null {
  if (points.length === 0) return null;
  let x = 0;
  let y = 0;
  for (const p of points) {
    x += p.x;
    y += p.y;
  }
  return { x: x / points.length, y: y / points.length };
}
