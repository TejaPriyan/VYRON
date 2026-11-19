/**
 * Level of detail, viewport culling and label placement for the map canvas.
 *
 * Pure geometry with no rendering dependency, so the same rules drive the WebGL renderer, the SVG
 * fallback and the tests. Coordinates are screen pixels unless a function says "world".
 */

export type DetailLevel = "clusters" | "nodes" | "labelled";

/** Zoom is a scale factor where 1 fits the whole graph in the viewport. */
export const LOD = {
  /** Below this the map shows cluster summaries instead of individual entities. */
  nodesFromZoom: 1.25,
  /** From this zoom up every entity that fits gets a name. */
  labelsFromZoom: 2.5,
  minZoom: 0.25,
  maxZoom: 16,
} as const;

export function clampZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return 1;
  return Math.min(LOD.maxZoom, Math.max(LOD.minZoom, zoom));
}

export function detailLevel(zoom: number): DetailLevel {
  const z = clampZoom(zoom);
  if (z < LOD.nodesFromZoom) return "clusters";
  if (z < LOD.labelsFromZoom) return "nodes";
  return "labelled";
}

export interface Rect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface Point {
  readonly x: number;
  readonly y: number;
}

/** True when a point is inside the viewport grown by `margin` pixels (so edges do not pop in). */
export function isVisible(point: Point, viewport: Rect, margin = 32): boolean {
  return (
    point.x >= viewport.x - margin &&
    point.x <= viewport.x + viewport.width + margin &&
    point.y >= viewport.y - margin &&
    point.y <= viewport.y + viewport.height + margin
  );
}

/** An edge needs drawing when either end is visible or its bounding box crosses the viewport. */
export function edgeIsVisible(a: Point, b: Point, viewport: Rect, margin = 32): boolean {
  if (isVisible(a, viewport, margin) || isVisible(b, viewport, margin)) return true;
  const left = Math.min(a.x, b.x);
  const right = Math.max(a.x, b.x);
  const top = Math.min(a.y, b.y);
  const bottom = Math.max(a.y, b.y);
  return (
    right >= viewport.x - margin &&
    left <= viewport.x + viewport.width + margin &&
    bottom >= viewport.y - margin &&
    top <= viewport.y + viewport.height + margin
  );
}

export interface View {
  readonly zoom: number;
  readonly centerX: number;
  readonly centerY: number;
}

/** The view that fits `bounds` (world units) in a viewport of `width` x `height` pixels. */
export function fitView(bounds: Rect, viewport: { width: number; height: number }, padding = 48): View {
  const innerW = Math.max(1, viewport.width - 2 * padding);
  const innerH = Math.max(1, viewport.height - 2 * padding);
  const centerX = bounds.x + bounds.width / 2;
  const centerY = bounds.y + bounds.height / 2;
  if (bounds.width <= 0 && bounds.height <= 0) return { zoom: LOD.labelsFromZoom, centerX, centerY };
  const scaleX = bounds.width > 0 ? innerW / bounds.width : Number.POSITIVE_INFINITY;
  const scaleY = bounds.height > 0 ? innerH / bounds.height : Number.POSITIVE_INFINITY;
  return { zoom: clampZoom(Math.min(scaleX, scaleY)), centerX, centerY };
}

export interface LabelCandidate {
  readonly id: string;
  /** Screen position of the mark the label belongs to. */
  readonly x: number;
  readonly y: number;
  /** Measured label size in pixels. */
  readonly width: number;
  readonly height: number;
  /** Higher wins a contested spot (for example the entity's PageRank score). */
  readonly importance: number;
}

export interface LabelOptions {
  /** Hard cap on labels drawn at once. */
  readonly budget: number;
  /** Ids that must be labelled (selected, hovered, search hit); they are placed first. */
  readonly pinned?: ReadonlySet<string>;
  /** Pixels of clear space required between two labels. */
  readonly gap?: number;
}

/**
 * Pick which labels to draw, every one to the right of its point: pinned ids first, then by importance,
 * skipping any that would overlap one already placed. Deterministic: ties break on id. Returns ids in
 * placement order. `placeLabels` is the fuller version that also tries the other sides and avoids marks.
 */
export function selectLabels(candidates: ReadonlyArray<LabelCandidate>, options: LabelOptions): string[] {
  const asMarks = candidates.map((c) => ({ ...c, radius: 0 }));
  return placeLabels(asMarks, { ...options, sides: ["right"], offset: 0 }).map((placement) => placement.id);
}

export type LabelSide = "right" | "left" | "above" | "below";

export const LABEL_SIDES: ReadonlyArray<LabelSide> = ["right", "left", "above", "below"];

/** A mark a label must not cover. `id` lets a label ignore the mark it belongs to. */
export interface LabelObstacle extends Rect {
  readonly id?: string;
}

export interface PlacedLabelCandidate {
  readonly id: string;
  /** Screen position of the mark's centre. */
  readonly x: number;
  readonly y: number;
  /** How far the mark reaches from its centre, in pixels; the label starts beyond it. */
  readonly radius: number;
  /** How far it reaches sideways when that differs from `radius` (a wide region); a label beside it starts beyond this. */
  readonly radiusX?: number;
  /** Measured label size in pixels. */
  readonly width: number;
  readonly height: number;
  readonly importance: number;
}

export interface PlacementOptions extends LabelOptions {
  /** Marks that labels must keep clear of (every drawn mark, labelled or not). */
  readonly obstacles?: ReadonlyArray<LabelObstacle>;
  /** Sides to try, in order of preference. */
  readonly sides?: ReadonlyArray<LabelSide>;
  /** Pixels between a mark and its label. */
  readonly offset?: number;
  /** When given, a label must lie wholly inside this rectangle (the viewport), so none is cut off. */
  readonly bounds?: Rect;
}

export interface LabelPlacement {
  readonly id: string;
  readonly side: LabelSide;
  /** Where to put the text: SVG `x` and `y` with `dominant-baseline: central` and the given anchor. */
  readonly x: number;
  readonly y: number;
  readonly anchor: "start" | "middle" | "end";
  /** The space the label occupies, for anything laid out after it. */
  readonly rect: Rect;
}

class RectGrid {
  private readonly cell: number;
  private readonly gap: number;
  private readonly buckets = new Map<string, Array<Rect & { id?: string }>>();

  constructor(cell: number, gap: number) {
    this.cell = cell;
    this.gap = gap;
  }

  private keys(r: Rect): string[] {
    const keys: string[] = [];
    const x0 = Math.floor((r.x - this.gap) / this.cell);
    const x1 = Math.floor((r.x + r.width + this.gap) / this.cell);
    const y0 = Math.floor((r.y - this.gap) / this.cell);
    const y1 = Math.floor((r.y + r.height + this.gap) / this.cell);
    for (let cx = x0; cx <= x1; cx += 1) for (let cy = y0; cy <= y1; cy += 1) keys.push(`${cx},${cy}`);
    return keys;
  }

  add(r: Rect & { id?: string }): void {
    for (const key of this.keys(r)) {
      const bucket = this.buckets.get(key);
      if (bucket) bucket.push(r);
      else this.buckets.set(key, [r]);
    }
  }

  /** True when `r` comes within `gap` of a stored rectangle other than the one owned by `ignoreId`. */
  clashes(r: Rect, ignoreId?: string): boolean {
    const gap = this.gap;
    for (const key of this.keys(r)) {
      for (const o of this.buckets.get(key) ?? []) {
        if (ignoreId !== undefined && o.id === ignoreId) continue;
        if (r.x < o.x + o.width + gap && o.x < r.x + r.width + gap && r.y < o.y + o.height + gap && o.y < r.y + r.height + gap) {
          return true;
        }
      }
    }
    return false;
  }
}

function contains(outer: Rect, inner: Rect): boolean {
  return (
    inner.x >= outer.x &&
    inner.y >= outer.y &&
    inner.x + inner.width <= outer.x + outer.width &&
    inner.y + inner.height <= outer.y + outer.height
  );
}

/** The area where two rectangles overlap; zero when they only touch or are apart. */
function overlapArea(a: Rect, b: Rect): number {
  const width = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const height = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return width > 0 && height > 0 ? width * height : 0;
}

function labelRect(c: PlacedLabelCandidate, side: LabelSide, offset: number): { rect: Rect; placement: LabelPlacement } {
  const reach = c.radius + offset;
  const reachX = (c.radiusX ?? c.radius) + offset;
  switch (side) {
    case "right":
      return {
        rect: { x: c.x + reachX, y: c.y - c.height / 2, width: c.width, height: c.height },
        placement: { id: c.id, side, x: c.x + reachX, y: c.y, anchor: "start", rect: { x: c.x + reachX, y: c.y - c.height / 2, width: c.width, height: c.height } },
      };
    case "left":
      return {
        rect: { x: c.x - reachX - c.width, y: c.y - c.height / 2, width: c.width, height: c.height },
        placement: { id: c.id, side, x: c.x - reachX, y: c.y, anchor: "end", rect: { x: c.x - reachX - c.width, y: c.y - c.height / 2, width: c.width, height: c.height } },
      };
    case "above":
      return {
        rect: { x: c.x - c.width / 2, y: c.y - reach - c.height, width: c.width, height: c.height },
        placement: { id: c.id, side, x: c.x, y: c.y - reach - c.height / 2, anchor: "middle", rect: { x: c.x - c.width / 2, y: c.y - reach - c.height, width: c.width, height: c.height } },
      };
    case "below":
      return {
        rect: { x: c.x - c.width / 2, y: c.y + reach, width: c.width, height: c.height },
        placement: { id: c.id, side, x: c.x, y: c.y + reach + c.height / 2, anchor: "middle", rect: { x: c.x - c.width / 2, y: c.y + reach, width: c.width, height: c.height } },
      };
  }
}

/**
 * Choose which labels to draw and on which side of their mark.
 *
 * Same order as `selectLabels` (pinned first, then importance, ties on id) but each label may sit to the
 * right, left, above or below its mark, and may not cover another mark or another label. A label with no
 * free side is skipped, except a pinned one, which is always placed so a selection is never left unnamed: on
 * the side where it covers the least (marks and names already placed count, and so does anything outside
 * `bounds`), the first of equals in the order of `sides`. Deterministic.
 */
export function placeLabels(candidates: ReadonlyArray<PlacedLabelCandidate>, options: PlacementOptions): LabelPlacement[] {
  const gap = options.gap ?? 4;
  const offset = options.offset ?? 5;
  const sides = options.sides && options.sides.length > 0 ? options.sides : LABEL_SIDES;
  const pinned = options.pinned ?? new Set<string>();
  const bounds = options.bounds;
  const ordered = [...candidates].sort(
    (a, b) =>
      Number(pinned.has(b.id)) - Number(pinned.has(a.id)) ||
      b.importance - a.importance ||
      (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
  const cell = Math.max(16, ...ordered.map((c) => Math.max(c.width, c.height) + gap + Math.max(c.radius, c.radiusX ?? 0)));
  const labels = new RectGrid(cell, gap);
  const marks = new RectGrid(cell, 1);
  for (const obstacle of options.obstacles ?? []) {
    if ([obstacle.x, obstacle.y, obstacle.width, obstacle.height].every(Number.isFinite)) marks.add(obstacle);
  }

  const obstacleRects = (options.obstacles ?? []).filter((o) => [o.x, o.y, o.width, o.height].every(Number.isFinite));
  /** How much `rect` would cover of what is already there, for a label that has to be placed somewhere. */
  const coverage = (rect: Rect, ownId: string): number => {
    let area = 0;
    for (const obstacle of obstacleRects) if (obstacle.id !== ownId) area += overlapArea(rect, obstacle);
    for (const label of placed) area += overlapArea(rect, label.rect);
    // Text cut off by the edge of the map counts as covered, at four times the weight of an overlap.
    if (bounds) area += 4 * (rect.width * rect.height - overlapArea(rect, bounds));
    return area;
  };

  const placed: LabelPlacement[] = [];
  for (const candidate of ordered) {
    if (placed.length >= options.budget) break;
    if (![candidate.width, candidate.height, candidate.x, candidate.y, candidate.radius, candidate.radiusX ?? 0].every(Number.isFinite)) continue;
    let chosen: { rect: Rect; placement: LabelPlacement } | undefined;
    for (const side of sides) {
      const option = labelRect(candidate, side, offset);
      if (bounds && !contains(bounds, option.rect)) continue;
      if (!labels.clashes(option.rect) && !marks.clashes(option.rect, candidate.id)) {
        chosen = option;
        break;
      }
    }
    if (!chosen && pinned.has(candidate.id)) {
      let least = Number.POSITIVE_INFINITY;
      for (const side of sides) {
        const option = labelRect(candidate, side, offset);
        const cost = coverage(option.rect, candidate.id);
        if (cost < least) {
          least = cost;
          chosen = option;
        }
      }
    }
    if (!chosen) continue;
    labels.add(chosen.rect);
    placed.push(chosen.placement);
  }
  return placed;
}

export interface EdgeLabelCandidate {
  readonly id: string;
  /** The drawn line, in screen pixels. */
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
  /** Measured label size in pixels. */
  readonly width: number;
  readonly height: number;
}

export interface EdgeLabelOptions {
  /** Rectangles to keep clear of: marks and the names already placed. */
  readonly blockers?: ReadonlyArray<Rect>;
  /** The label must lie wholly inside this rectangle. */
  readonly bounds?: Rect;
  /** Pixels of clear space around a label. */
  readonly gap?: number;
}

const EDGE_LABEL_STOPS: ReadonlyArray<number> = [0.5, 0.4, 0.6, 0.3, 0.7];

/**
 * Put each relationship's phrase beside its line, in the given order. A label tries the middle of the line
 * first, then points either side of it, on one side of the line and then the other, and takes the first
 * spot that covers no blocker and no label already placed. A label with no such spot is left out of the
 * result: the caller hides it, because a phrase printed over another is worse than none (the connections
 * list in the details panel still says it). Returns the centre of each placed label.
 */
export function placeEdgeLabels(edges: ReadonlyArray<EdgeLabelCandidate>, options: EdgeLabelOptions = {}): Map<string, Point> {
  const gap = options.gap ?? 3;
  const taken: Rect[] = [...(options.blockers ?? [])].filter((r) => [r.x, r.y, r.width, r.height].every(Number.isFinite));
  const out = new Map<string, Point>();
  const clashes = (r: Rect): boolean =>
    taken.some((o) => r.x < o.x + o.width + gap && o.x < r.x + r.width + gap && r.y < o.y + o.height + gap && o.y < r.y + r.height + gap);
  for (const edge of edges) {
    if (![edge.x1, edge.y1, edge.x2, edge.y2, edge.width, edge.height].every(Number.isFinite)) continue;
    const dx = edge.x2 - edge.x1;
    const dy = edge.y2 - edge.y1;
    const length = Math.hypot(dx, dy);
    if (length === 0) continue;
    const nx = -dy / length;
    const ny = dx / length;
    // Far enough off the line that the text clears it whichever way the line runs.
    const off = Math.abs(nx) * (edge.width / 2) + Math.abs(ny) * (edge.height / 2) + 3;
    let found: { point: Point; rect: Rect } | undefined;
    search: for (const t of EDGE_LABEL_STOPS) {
      for (const sign of [1, -1]) {
        const point = { x: edge.x1 + dx * t + nx * off * sign, y: edge.y1 + dy * t + ny * off * sign };
        const rect = { x: point.x - edge.width / 2, y: point.y - edge.height / 2, width: edge.width, height: edge.height };
        if (options.bounds && !contains(options.bounds, rect)) continue;
        if (clashes(rect)) continue;
        found = { point, rect };
        break search;
      }
    }
    if (!found) continue;
    taken.push(found.rect);
    out.set(edge.id, found.point);
  }
  return out;
}

/** How many labels the canvas may draw, scaled to its area and capped so text never becomes noise. */
export function labelBudget(level: DetailLevel, viewport: { width: number; height: number }): number {
  const area = Math.max(0, viewport.width) * Math.max(0, viewport.height);
  const perMegapixel = level === "clusters" ? 8 : level === "nodes" ? 24 : 60;
  return Math.max(4, Math.min(120, Math.round((area / 1_000_000) * perMegapixel)));
}
