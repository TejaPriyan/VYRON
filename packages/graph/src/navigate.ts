/**
 * Keyboard movement around the map.
 *
 * The map is one tab stop (a roving tabindex); arrow keys then move to the nearest entity in that
 * direction, so a keyboard user can follow a cluster without tabbing through every node. Positions
 * are on-screen positions; nothing here depends on the DOM.
 */
import type { Point } from "./lod.ts";

export type Direction = "left" | "right" | "up" | "down";

export interface Positioned extends Point {
  readonly id: string;
}

const VECTORS: Readonly<Record<Direction, Point>> = {
  left: { x: -1, y: 0 },
  right: { x: 1, y: 0 },
  up: { x: 0, y: -1 },
  down: { x: 0, y: 1 },
};

/** Candidates must lie within 60 degrees of the direction; the rest are "behind" or off to the side. */
const MIN_COSINE = Math.cos(Math.PI / 3);

/**
 * The entity nearest to `fromId` in `direction`, or null when none lies that way (the caller keeps the
 * focus where it is). Angle counts as well as distance, so a close node off to the side does not
 * beat a slightly farther one straight ahead. Ties break on id.
 */
export function nextInDirection(items: ReadonlyArray<Positioned>, fromId: string, direction: Direction): string | null {
  const from = items.find((item) => item.id === fromId);
  if (!from) return null;
  const vector = VECTORS[direction];
  let best: { id: string; cost: number } | null = null;
  for (const item of items) {
    if (item.id === fromId) continue;
    const dx = item.x - from.x;
    const dy = item.y - from.y;
    const distance = Math.hypot(dx, dy);
    if (distance === 0) continue;
    const cosine = (dx * vector.x + dy * vector.y) / distance;
    if (cosine < MIN_COSINE) continue;
    const cost = distance * (2 - cosine);
    if (best === null || cost < best.cost || (cost === best.cost && item.id < best.id)) best = { id: item.id, cost };
  }
  return best?.id ?? null;
}

/** Top-to-bottom, then left-to-right: the order Home/End and a first Tab land in. Ties break on id. */
export function readingOrder(items: ReadonlyArray<Positioned>): string[] {
  return [...items]
    .sort((a, b) => a.y - b.y || a.x - b.x || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .map((item) => item.id);
}
