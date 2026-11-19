/**
 * The map camera: pan and zoom maths with no DOM in it.
 *
 * `zoom` is relative to the fit view, as in `lod.ts`: 1 shows the whole graph with a margin, 2 shows it
 * twice as large. The margin is exactly the gap between zoom 1 and `LOD.nodesFromZoom`, so at that
 * zoom the graph just fills the viewport and every entity is drawn. A graph small enough to draw in
 * full opens there; a large one opens at zoom 1 and shows cluster summaries first.
 */
import { clampZoom, LOD } from "./lod.ts";
import type { Point, Rect } from "./lod.ts";

export interface Size {
  readonly width: number;
  readonly height: number;
}

/** `x` and `y` are the world point at the centre of the viewport. */
export interface Camera {
  readonly x: number;
  readonly y: number;
  readonly zoom: number;
}

/** A graph with at most this many entities opens at entity level rather than as cluster summaries. */
export const SMALL_GRAPH_NODES = 60;

/**
 * Pixels of the viewport's shorter side that each square-root-of-an-entity needs before the opening view draws
 * entities. The graph is fitted to the shorter side, so 60 entities want about 620 px, which a desktop map
 * has and a phone does not: on a phone the opening view is the cluster summaries, and a tap on one zooms in.
 */
export const ENTITY_SPAN_PX = 80;

/**
 * The zoom a graph opens at. A small graph opens at entity level, so it is read whole, but only when the viewport
 * is big enough for that to stay legible; anything else opens at cluster level. Pass the viewport when it is
 * known: without one (or with one that has no size yet) only the entity count decides.
 */
export function initialZoom(nodeCount: number, viewport?: Size): number {
  if (nodeCount > SMALL_GRAPH_NODES) return 1;
  const measured = viewport !== undefined && viewport.width > 0 && viewport.height > 0;
  if (measured && Math.min(viewport.width, viewport.height) < ENTITY_SPAN_PX * Math.sqrt(nodeCount)) return 1;
  return LOD.nodesFromZoom;
}

/** The smallest rectangle holding all points, grown by `padding` on every side; null for no points. */
export function boundsOf(points: ReadonlyArray<Point>, padding = 0): Rect | null {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const p of points) {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x);
    maxY = Math.max(maxY, p.y);
  }
  if (minX === Number.POSITIVE_INFINITY) return null;
  return { x: minX - padding, y: minY - padding, width: maxX - minX + 2 * padding, height: maxY - minY + 2 * padding };
}

/** Pixels per world unit at zoom 1: the scale at which `bounds` fills the viewport at `LOD.nodesFromZoom`. */
export function fitScale(bounds: Rect, viewport: Size, padding = 48): number {
  const innerW = Math.max(1, viewport.width - 2 * padding);
  const innerH = Math.max(1, viewport.height - 2 * padding);
  const w = Math.max(bounds.width, 1e-9);
  const h = Math.max(bounds.height, 1e-9);
  return Math.min(innerW / w, innerH / h) / LOD.nodesFromZoom;
}

export function centreOn(bounds: Rect, zoom: number): Camera {
  return { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2, zoom: clampZoom(zoom) };
}

export function worldToScreen(camera: Camera, scale: number, viewport: Size, p: Point): Point {
  const k = scale * camera.zoom;
  return { x: viewport.width / 2 + (p.x - camera.x) * k, y: viewport.height / 2 + (p.y - camera.y) * k };
}

export function screenToWorld(camera: Camera, scale: number, viewport: Size, p: Point): Point {
  const k = scale * camera.zoom;
  return { x: camera.x + (p.x - viewport.width / 2) / k, y: camera.y + (p.y - viewport.height / 2) / k };
}

/** Zoom by `factor` keeping the world point under `anchor` (a screen point) where it is. */
export function zoomAt(camera: Camera, factor: number, anchor: Point, scale: number, viewport: Size): Camera {
  if (!(factor > 0) || !Number.isFinite(factor)) return camera;
  const zoom = clampZoom(camera.zoom * factor);
  if (zoom === camera.zoom) return camera;
  const under = screenToWorld(camera, scale, viewport, anchor);
  const k = scale * zoom;
  return {
    x: under.x - (anchor.x - viewport.width / 2) / k,
    y: under.y - (anchor.y - viewport.height / 2) / k,
    zoom,
  };
}

/** Move the view by a screen-space drag of (`dx`, `dy`) pixels: the map follows the pointer. */
export function panBy(camera: Camera, dx: number, dy: number, scale: number): Camera {
  const k = scale * camera.zoom;
  return { ...camera, x: camera.x - dx / k, y: camera.y - dy / k };
}

export const easeOutCubic = (t: number): number => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);

/** Between two cameras; zoom moves in log space so a zoom-in and its reverse feel the same. */
export function lerpCamera(from: Camera, to: Camera, t: number): Camera {
  const u = Math.min(1, Math.max(0, t));
  return {
    x: from.x + (to.x - from.x) * u,
    y: from.y + (to.y - from.y) * u,
    zoom: Math.exp(Math.log(from.zoom) + (Math.log(to.zoom) - Math.log(from.zoom)) * u),
  };
}

/** The zoom at which `bounds` fills the viewport (inside `padding`), kept inside the allowed zoom range. */
export function zoomToFit(bounds: Rect, scale: number, viewport: Size, padding = 48): number {
  const innerW = Math.max(1, viewport.width - 2 * padding);
  const innerH = Math.max(1, viewport.height - 2 * padding);
  const w = Math.max(bounds.width, 1e-9);
  const h = Math.max(bounds.height, 1e-9);
  return clampZoom(Math.min(innerW / w, innerH / h) / scale);
}
