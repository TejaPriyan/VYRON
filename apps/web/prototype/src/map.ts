/**
 * The map: an SVG drawing of the atlas with pan, zoom, selection and keyboard movement.
 *
 * Geometry comes from `packages/graph` (layout, camera, level of detail, label placement, territories,
 * glyph shapes, arrow-key navigation); this file only turns it into elements and wires input to it.
 * Symbols keep their pixel size while positions scale with zoom, the way a printed atlas does.
 *
 * SVG is the right tool for a few hundred entities: every mark is a real element a screen reader and
 * a test can see. It is not a claim about ten thousand; that is the WebGL renderer's job (UNVERIFIED,
 * not built).
 */
import { boundsOf, centreOn, easeOutCubic, fitScale, initialZoom, lerpCamera, panBy, SMALL_GRAPH_NODES, screenToWorld, worldToScreen, zoomAt, zoomToFit } from "../../../../packages/graph/src/camera.ts";
import type { Camera, Size } from "../../../../packages/graph/src/camera.ts";
import type { Atlas } from "../../../../packages/graph/src/atlas.ts";
import { GLYPH_REACH, glyphPath } from "../../../../packages/graph/src/glyph.ts";
import { meanPoint, paddedHull, polygonPath } from "../../../../packages/graph/src/hull.ts";
import type { Layout } from "../../../../packages/graph/src/layout.ts";
import { detailLevel, labelBudget, LOD, placeEdgeLabels, placeLabels } from "../../../../packages/graph/src/lod.ts";
import type { EdgeLabelCandidate, LabelObstacle, LabelPlacement, PlacedLabelCandidate } from "../../../../packages/graph/src/lod.ts";
import type { DetailLevel, Point, Rect } from "../../../../packages/graph/src/lod.ts";
import { nextInDirection, readingOrder } from "../../../../packages/graph/src/navigate.ts";
import type { Direction } from "../../../../packages/graph/src/navigate.ts";
import { RELATION_PHRASES } from "../../../../packages/graph/src/outline.ts";
import type { EntityKind } from "../../../../packages/types/src/api.ts";
import { kindVisual, provenanceVisual } from "../../../../packages/ui/src/tokens.ts";
import { h, plural, s } from "./dom.ts";
import { controlIcon } from "./icons.ts";

export type Selection = { readonly kind: "entity"; readonly id: string } | { readonly kind: "cluster"; readonly id: string } | null;

export interface MapPath {
  readonly nodeIds: ReadonlySet<string>;
  readonly edgeKeys: ReadonlySet<string>;
}

export interface MapFilters {
  /** Kinds to show; empty means all. */
  readonly kinds: ReadonlySet<EntityKind>;
  /** Entities that matched a search, or null when nothing is being searched for. */
  readonly matches: ReadonlySet<string> | null;
  /** Entities left out because their first event is after the year the map is shown as of. Empty when no year is set. */
  readonly later: ReadonlySet<string>;
  /** Entities with no date, drawn dimmed while a year is chosen because the year says nothing about them. */
  readonly undated: ReadonlySet<string>;
}

export interface MapViewOptions {
  readonly host: HTMLElement;
  readonly atlas: Atlas;
  readonly layout: Layout;
  readonly onSelect: (selection: Selection) => void;
  readonly onView?: (view: { readonly level: DetailLevel; readonly zoom: number }) => void;
}

const SVG_ARROW = "vy-arrow";
const TERRITORY_PADDING = 0.5; // world units around a cluster's members
const MIN_CLUSTER_PADDING_PX = 22;
const TERRITORY_NAME_HEIGHT = 22;
/** How far a mark reaches with its provenance outline (drawn at 1.32 times the glyph), for clearance. */
const MARK_REACH = GLYPH_REACH * 1.32;
const TERRITORY_TEXT_HEIGHT = 38; // the name and the count under it
const FIT_PADDING_PX = 56;
const LABEL_HEIGHT = 16;
const EDGE_LABEL_HEIGHT = 14;
const DRAG_THRESHOLD = 4;
const KEY_PAN = 80;
const KEY_ZOOM = 1.4;
const MAX_EDGE_LABELS = 8;

/** The smallest "round" world spacing (1, 2 or 5 times a power of ten) that is at least 72 pixels on screen. */
function gridStep(pixelsPerUnit: number): number {
  for (let exponent = -3; exponent <= 6; exponent += 1) {
    for (const mantissa of [1, 2, 5]) {
      const step = mantissa * 10 ** exponent;
      if (step * pixelsPerUnit >= 72) return step;
    }
  }
  return 1e6;
}

interface NodeParts {
  readonly entityId: string;
  readonly root: SVGGElement;
  readonly radius: number;
  readonly label: SVGTextElement;
}

interface EdgeParts {
  readonly key: string;
  readonly sourceId: string;
  readonly targetId: string;
  readonly type: keyof typeof RELATION_PHRASES;
  readonly path: SVGPathElement;
  readonly label: SVGTextElement;
}

interface TerritoryParts {
  readonly clusterId: string;
  readonly root: SVGGElement;
  readonly hull: SVGPathElement;
  readonly name: SVGTextElement;
  readonly count: SVGTextElement;
}

interface LinkParts {
  readonly a: string;
  readonly b: string;
  /** Relationships between the two clusters. */
  readonly count: number;
  readonly path: SVGPathElement;
  readonly label: SVGTextElement;
}

export class MapView {
  private readonly host: HTMLElement;
  private readonly atlas: Atlas;
  private readonly layout: Layout;
  private readonly onSelect: (selection: Selection) => void;
  private readonly onView: ((view: { level: DetailLevel; zoom: number }) => void) | undefined;

  private readonly svg: SVGSVGElement;
  private readonly graticule: SVGPathElement;
  private readonly nodes = new Map<string, NodeParts>();
  private readonly edges: EdgeParts[] = [];
  private readonly territories = new Map<string, TerritoryParts>();
  private readonly links: LinkParts[] = [];
  private readonly readout: HTMLElement;
  private readonly resizeObserver: ResizeObserver;
  private readonly measureContext: CanvasRenderingContext2D | null;
  private readonly measured = new Map<string, number>();
  private readonly pointers = new Map<number, Point>();

  private size: Size = { width: 0, height: 0 };
  private insets = { right: 0, bottom: 0 };
  private scale = 1;
  private camera: Camera = { x: 0, y: 0, zoom: 1 };
  private readonly allBounds: Rect | null;
  private selection: Selection = null;
  private tracedPath: MapPath | null = null;
  private filters: MapFilters = { kinds: new Set(), matches: null, later: new Set(), undated: new Set() };
  private hovered: string | null = null;
  private tabStop: string | null = null;
  private frame = 0;
  private animation = 0;
  private gesture: { startedOn: Element | null; startX: number; startY: number; moved: boolean; pinch: number | null } | null = null;
  private lastReported = "";
  /** Marks and entity names as last drawn, which relationship phrases must keep clear of. */
  private blockers: Rect[] = [];
  /** Where the region names were put, which entity names keep clear of. */
  private territoryRects: Rect[] = [];
  private edgeLabelQueue: Array<{ edge: EdgeParts; x1: number; y1: number; x2: number; y2: number; width: number; height: number }> = [];
  private screen = new Map<string, Point>();

  constructor(options: MapViewOptions) {
    this.host = options.host;
    this.atlas = options.atlas;
    this.layout = options.layout;
    this.onSelect = options.onSelect;
    this.onView = options.onView;
    this.allBounds = boundsOf([...this.layout.positions.values()], TERRITORY_PADDING + 0.1);

    this.measureContext = document.createElement("canvas").getContext("2d");
    this.graticule = s("path", { class: "graticule" });
    const defs = s("defs", {}, this.arrowMarker(SVG_ARROW, "arrow-head"), this.arrowMarker(`${SVG_ARROW}-active`, "arrow-head is-active"));
    const territoryLayer = s("g", { class: "territories", "aria-hidden": "true" });
    const linkLayer = s("g", { class: "cluster-links", "aria-hidden": "true" });
    const edgeLayer = s("g", { class: "edges", "aria-hidden": "true" });
    const edgeLabelLayer = s("g", { class: "edge-labels", "aria-hidden": "true" });
    const nodeLayer = s("g", { class: "nodes" });
    const labelLayer = s("g", { class: "labels", "aria-hidden": "true" });

    this.svg = s(
      "svg",
      {
        class: "map",
        role: "group",
        "aria-label": `Map of technologies: ${plural(this.atlas.entities.length, "entity", "entities")} in ${plural(this.atlas.clusters.length, "cluster", "clusters")}. Focus an entity, then use the arrow keys to move to its nearest neighbour and Enter to select it.`,
        "data-lod": "nodes",
      },
      defs,
      this.graticule,
      territoryLayer,
      linkLayer,
      edgeLayer,
      edgeLabelLayer,
      nodeLayer,
      labelLayer,
    );

    for (const cluster of this.atlas.clusters) {
      const hull = s("path", { class: "hull" });
      const name = s("text", { class: "territory-name" }, cluster.label);
      const count = s("text", { class: "territory-count" }, String(cluster.size));
      const root = s("g", { class: "territory", "data-cluster": cluster.id }, hull, name, count);
      territoryLayer.append(root);
      this.territories.set(cluster.id, { clusterId: cluster.id, root, hull, name, count });
    }

    for (const link of this.atlas.clusterLinks) {
      const dash = this.dashFor(link.style);
      const path = s("path", { class: "cluster-link", fill: "none", "stroke-dasharray": dash });
      const label = s("text", { class: "cluster-link-count" }, String(link.count));
      linkLayer.append(path, label);
      this.links.push({ a: link.a, b: link.b, count: link.count, path, label });
    }

    for (const relationship of this.atlas.relationships) {
      const dash = provenanceVisual(relationship.provenance).dash;
      const path = s("path", { class: "edge", fill: "none", ...(dash === "none" ? {} : { "stroke-dasharray": dash }) });
      const label = s("text", { class: "edge-label is-off" });
      edgeLayer.append(path);
      edgeLabelLayer.append(label);
      this.edges.push({
        key: `${relationship.source_id}|${relationship.type}|${relationship.target_id}`,
        sourceId: relationship.source_id,
        targetId: relationship.target_id,
        type: relationship.type,
        path,
        label,
      });
    }

    for (const entity of this.atlas.entities) {
      const visual = kindVisual(entity.kind);
      const provenance = provenanceVisual(entity.provenance);
      const radius = 5.5 + 5.5 * this.atlas.importance(entity.id);
      const connections = this.atlas.connections.get(entity.id)?.length ?? 0;
      const summary = `${entity.name}, ${visual.label.toLowerCase()}. ${plural(connections, "connection", "connections")}. ${provenance.label}.`;
      const root = s(
        "g",
        { class: "node", role: "button", tabindex: -1, "data-id": entity.id, "aria-label": summary, "aria-pressed": "false" },
        s("title", {}, `${entity.name} (${visual.label})`),
        s("circle", { class: "hit", r: 16 }),
        s("path", { class: "ring", d: glyphPath(visual.shape, radius * 1.55) }),
        s("path", {
          class: "provenance-outline",
          d: glyphPath(visual.shape, radius * 1.32),
          fill: "none",
          stroke: visual.color,
          ...(provenance.dash === "none" ? {} : { "stroke-dasharray": provenance.dash }),
        }),
        s("path", { class: "glyph", d: glyphPath(visual.shape, radius), fill: visual.color }),
      );
      const label = s("text", { class: "label is-off" }, entity.name);
      nodeLayer.append(root);
      labelLayer.append(label);
      this.nodes.set(entity.id, { entityId: entity.id, root, radius, label });
    }

    this.readout = h("p", { class: "map-readout", "aria-hidden": "true" });
    const controls = h(
      "div",
      { class: "map-controls", role: "group", "aria-label": "Map controls" },
      this.controlButton("Zoom in", "plus", () => this.zoomBy(KEY_ZOOM)),
      this.controlButton("Zoom out", "minus", () => this.zoomBy(1 / KEY_ZOOM)),
      this.controlButton("Fit the whole map", "fit", () => this.fit(true)),
    );
    this.host.append(this.svg, controls, this.readout);

    this.svg.addEventListener("pointerdown", (event) => this.onPointerDown(event));
    this.svg.addEventListener("pointermove", (event) => this.onPointerMove(event));
    this.svg.addEventListener("pointerup", (event) => this.onPointerEnd(event));
    this.svg.addEventListener("pointercancel", (event) => this.onPointerEnd(event));
    this.svg.addEventListener("wheel", (event) => this.onWheel(event), { passive: false });
    this.svg.addEventListener("click", (event) => this.onClick(event));
    this.svg.addEventListener("pointerover", (event) => this.onHover(event, true));
    this.svg.addEventListener("pointerout", (event) => this.onHover(event, false));
    this.svg.addEventListener("focusin", (event) => this.onFocusIn(event));
    this.host.addEventListener("keydown", (event) => this.onKeyDown(event));

    this.resizeObserver = new ResizeObserver(() => this.measure());
    this.resizeObserver.observe(this.host);
    this.measure();
    this.fit(false);
    this.applyState();
  }

  // ---- public ---------------------------------------------------------------------------------

  setSelection(selection: Selection): void {
    this.selection = selection;
    this.applyState();
    this.schedule();
  }

  setPath(path: MapPath | null): void {
    this.tracedPath = path;
    this.applyState();
    this.schedule();
  }

  setFilters(filters: MapFilters): void {
    this.filters = filters;
    this.applyState();
    this.schedule();
  }

  /**
   * Reserve space the map should not draw into (a panel laid over it). The view recentres on what is left.
   * Returns whether anything changed, so a caller that framed a selection knows to frame it again.
   */
  setInsets(insets: { right: number; bottom: number }): boolean {
    const right = Math.max(0, Math.round(insets.right));
    const bottom = Math.max(0, Math.round(insets.bottom));
    if (right === this.insets.right && bottom === this.insets.bottom) return false;
    this.insets = { right, bottom };
    this.measure();
    return true;
  }

  /** The opening view: everything in frame, at entity level only when the space left on the map can carry it. */
  fit(animate: boolean): void {
    if (!this.allBounds) return;
    this.moveTo(centreOn(this.allBounds, initialZoom(this.atlas.entities.length, this.viewport())), animate);
  }

  zoomBy(factor: number): void {
    const viewport = this.viewport();
    this.cancelAnimation();
    this.camera = zoomAt(this.camera, factor, { x: viewport.width / 2, y: viewport.height / 2 }, this.scale, viewport);
    this.schedule();
  }

  /** Move the camera to show a selection; the selection itself is set separately with `setSelection`. */
  focus(selection: Selection, animate = true): void {
    if (!selection) return;
    const viewport = this.viewport();
    const points: Point[] = [];
    let padding = 0.7;
    // Zooming in on a small neighbourhood to the limit leaves one lonely pair of marks, so stay at the level
    // where every name is already shown and the neighbours still frame the selection.
    let maxZoom = 3;
    if (selection.kind === "entity") {
      const own = this.layout.positions.get(selection.id);
      if (!own) return;
      points.push(own);
      for (const connection of this.atlas.connections.get(selection.id) ?? []) {
        const other = this.layout.positions.get(connection.otherId);
        if (other && this.isShown(connection.otherId)) points.push(other);
      }
    } else {
      const cluster = this.atlas.clusters.find((c) => c.id === selection.id);
      if (!cluster) return;
      for (const id of cluster.memberIds) {
        const position = this.layout.positions.get(id);
        if (position && this.isShown(id)) points.push(position);
      }
      padding = 0.9;
      maxZoom = 2.5;
    }
    const bounds = boundsOf(points, padding);
    if (!bounds) return;
    const zoom = Math.min(maxZoom, Math.max(LOD.nodesFromZoom, zoomToFit(bounds, this.scale, viewport, FIT_PADDING_PX + 16)));
    this.moveTo(centreOn(bounds, zoom), animate);
  }

  focusNode(id: string): void {
    this.nodes.get(id)?.root.focus({ preventScroll: true });
  }

  /** Frame a set of entities (for example every hop along a shortest path). */
  focusNodes(ids: ReadonlyArray<string>, animate = true): void {
    const points: Point[] = [];
    for (const id of ids) {
      const point = this.layout.positions.get(id);
      if (point && this.isShown(id)) points.push(point);
    }
    const bounds = boundsOf(points, 0.85);
    if (!bounds) return;
    const viewport = this.viewport();
    const zoom = Math.min(3, Math.max(LOD.nodesFromZoom, zoomToFit(bounds, this.scale, viewport, FIT_PADDING_PX + 20)));
    this.moveTo(centreOn(bounds, zoom), animate);
  }

  destroy(): void {
    this.resizeObserver.disconnect();
    this.cancelAnimation();
    if (this.frame) cancelAnimationFrame(this.frame);
    this.svg.remove();
  }

  // ---- geometry -------------------------------------------------------------------------------

  private viewport(): Size {
    return { width: Math.max(1, this.size.width - this.insets.right), height: Math.max(1, this.size.height - this.insets.bottom) };
  }

  private measure(): void {
    const rect = this.host.getBoundingClientRect();
    this.size = { width: rect.width, height: rect.height };
    if (this.allBounds) this.scale = fitScale(this.allBounds, this.viewport(), FIT_PADDING_PX);
    this.schedule();
  }

  private schedule(): void {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.render();
    });
  }

  private reducedMotion(): boolean {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  private cancelAnimation(): void {
    if (this.animation) cancelAnimationFrame(this.animation);
    this.animation = 0;
  }

  private moveTo(target: Camera, animate: boolean, duration = 600): void {
    this.cancelAnimation();
    if (!animate || this.reducedMotion()) {
      this.camera = target;
      this.schedule();
      return;
    }
    const from = this.camera;
    const start = performance.now();
    const step = (now: number): void => {
      const t = Math.min(1, (now - start) / duration);
      this.camera = lerpCamera(from, target, easeOutCubic(t));
      this.render();
      this.animation = t < 1 ? requestAnimationFrame(step) : 0;
    };
    this.animation = requestAnimationFrame(step);
  }

  private isShown(id: string): boolean {
    const entity = this.atlas.byId.get(id);
    if (!entity) return false;
    if (this.filters.later.has(id)) return false;
    return this.filters.kinds.size === 0 || this.filters.kinds.has(entity.kind);
  }

  private dashFor(style: "verified" | "derived" | "conjecture" | "working"): string {
    const dash = { verified: "none", derived: "6 3", conjecture: "1 4", working: "8 3 1 3" }[style];
    return dash === "none" ? "" : dash;
  }

  // ---- state classes (change on selection and filter, not on every frame) ---------------------

  private relatedIds(): Set<string> {
    const related = new Set<string>();
    if (this.selection?.kind === "entity") {
      for (const connection of this.atlas.connections.get(this.selection.id) ?? []) related.add(connection.otherId);
    } else if (this.selection?.kind === "cluster") {
      const cluster = this.atlas.clusters.find((c) => c.id === this.selection?.id);
      for (const id of cluster?.memberIds ?? []) related.add(id);
    }
    return related;
  }

  private applyState(): void {
    const related = this.relatedIds();
    const { matches } = this.filters;
    const pathNodes = this.tracedPath?.nodeIds ?? null;
    const pathEdges = this.tracedPath?.edgeKeys ?? null;
    const selectedId = this.selection?.kind === "entity" ? this.selection.id : null;
    const hasFocus = this.selection !== null || matches !== null || pathNodes !== null;
    this.svg.classList.toggle("has-focus", hasFocus);
    this.svg.classList.toggle("has-path", pathNodes !== null);
    for (const [id, node] of this.nodes) {
      const shown = this.isShown(id);
      node.root.classList.toggle("is-hidden", !shown);
      node.root.classList.toggle("is-selected", id === selectedId);
      node.root.classList.toggle("is-related", related.has(id));
      node.root.classList.toggle("is-path", pathNodes?.has(id) ?? false);
      node.root.classList.toggle("is-match", matches?.has(id) ?? false);
      node.root.classList.toggle("is-undated", this.filters.undated.has(id));
      node.root.setAttribute("aria-pressed", id === selectedId ? "true" : "false");
      if (!shown && this.tabStop === id) this.tabStop = null;
    }
    for (const edge of this.edges) {
      const shown = this.isShown(edge.sourceId) && this.isShown(edge.targetId);
      const onPath = pathEdges?.has(edge.key) ?? false;
      const active =
        onPath ||
        (pathEdges === null &&
          ((selectedId !== null && (edge.sourceId === selectedId || edge.targetId === selectedId)) ||
            (this.selection?.kind === "cluster" && related.has(edge.sourceId) && related.has(edge.targetId))));
      edge.path.classList.toggle("is-hidden", !shown);
      edge.path.classList.toggle("is-path", shown && onPath);
      edge.path.classList.toggle("is-active", shown && active);
      edge.path.classList.toggle("is-dim", shown && !active && hasFocus);
    }
    for (const [clusterId, territory] of this.territories) {
      territory.root.classList.toggle("is-selected", this.selection?.kind === "cluster" && this.selection.id === clusterId);
    }
    this.refreshTabStop();
  }

  private refreshTabStop(): void {
    const items = this.visibleNodePositions();
    const order = readingOrder(items);
    if (this.tabStop === null || !order.includes(this.tabStop)) this.tabStop = this.selection?.kind === "entity" && order.includes(this.selection.id) ? this.selection.id : (order[0] ?? null);
    for (const [id, node] of this.nodes) node.root.setAttribute("tabindex", id === this.tabStop ? "0" : "-1");
  }

  private visibleNodePositions(): Array<Point & { id: string }> {
    const out: Array<Point & { id: string }> = [];
    for (const [id] of this.nodes) {
      if (!this.isShown(id)) continue;
      const position = this.layout.positions.get(id);
      if (position) out.push({ id, ...worldToScreen(this.camera, this.scale, this.viewport(), position) });
    }
    return out;
  }

  // ---- drawing --------------------------------------------------------------------------------

  private render(): void {
    const viewport = this.viewport();
    if (viewport.width < 8 || viewport.height < 8) return;
    const level = detailLevel(this.camera.zoom);
    this.svg.setAttribute("data-lod", level);
    this.svg.setAttribute("data-zoom", this.camera.zoom.toFixed(2));
    const k = this.scale * this.camera.zoom;

    this.screen = new Map();
    for (const [id, position] of this.layout.positions) this.screen.set(id, worldToScreen(this.camera, this.scale, viewport, position));

    this.drawGraticule(viewport, k);
    this.drawTerritories(level, k, viewport);
    this.drawClusterLinks(level);
    this.drawEdges(level);
    this.drawNodes();
    this.drawLabels(level, viewport);
    this.drawEdgeLabels(viewport);

    const key = `${level}:${this.camera.zoom.toFixed(1)}`;
    if (key !== this.lastReported) {
      this.lastReported = key;
      const words = { clusters: "Clusters", nodes: "Entities", labelled: "Entities and names" }[level];
      this.readout.textContent = `${this.camera.zoom.toFixed(1)}× · ${words}`;
      this.onView?.({ level, zoom: this.camera.zoom });
    }
  }

  private drawGraticule(viewport: Size, k: number): void {
    const step = gridStep(k);
    const topLeft = screenToWorld(this.camera, this.scale, viewport, { x: 0, y: 0 });
    const bottomRight = screenToWorld(this.camera, this.scale, viewport, { x: this.size.width, y: this.size.height });
    const parts: string[] = [];
    let count = 0;
    for (let x = Math.ceil(topLeft.x / step) * step; x <= bottomRight.x && count < 80; x += step, count += 1) {
      const sx = Math.round(viewport.width / 2 + (x - this.camera.x) * k) + 0.5;
      parts.push(`M${sx} 0V${this.size.height}`);
    }
    for (let y = Math.ceil(topLeft.y / step) * step; y <= bottomRight.y && count < 160; y += step, count += 1) {
      const sy = Math.round(viewport.height / 2 + (y - this.camera.y) * k) + 0.5;
      parts.push(`M0 ${sy}H${this.size.width}`);
    }
    this.graticule.setAttribute("d", parts.join(""));
  }

  private drawTerritories(level: DetailLevel, k: number, viewport: Size): void {
    // Zoomed far out a territory would shrink to a speck; keep it large enough to read as a region.
    const padding = Math.max(TERRITORY_PADDING * k, level === "clusters" ? MIN_CLUSTER_PADDING_PX : 0);
    const shapes: Array<{ id: string; parts: TerritoryParts; centre: Point; top: number; bottom: number; left: number; right: number; box: Rect }> = [];
    for (const cluster of this.atlas.clusters) {
      const parts = this.territories.get(cluster.id);
      if (!parts) continue;
      const points = cluster.memberIds.filter((id) => this.isShown(id)).flatMap((id) => (this.screen.get(id) ? [this.screen.get(id) as Point] : []));
      parts.root.classList.toggle("is-hidden", points.length === 0);
      if (points.length === 0) continue;
      const hull = paddedHull(points, padding, 14);
      parts.hull.setAttribute("d", polygonPath(hull));
      const xs = hull.map((p) => p.x);
      const ys = hull.map((p) => p.y);
      const top = Math.min(...ys);
      const bottom = Math.max(...ys);
      const left = Math.min(...xs);
      const right = Math.max(...xs);
      const shown = points.length;
      parts.count.textContent = shown === cluster.size ? String(cluster.size) : `${shown} of ${cluster.size}`;
      shapes.push({
        id: cluster.id,
        parts,
        centre: meanPoint(points) as Point,
        top,
        bottom,
        left,
        right,
        box: { x: left, y: top, width: right - left, height: bottom - top },
      });
    }
    // Each name goes above, below or beside its region, whichever is clear of the marks (or, zoomed out, of the
    // other regions) and of the other names. A name is never left out, because a region without one is unreadable;
    // when no side is clear it takes the one where it covers least.
    const candidates: PlacedLabelCandidate[] = shapes.map(({ id, parts, centre, top, bottom, left, right }) => ({
      id,
      x: centre.x,
      y: (top + bottom) / 2,
      radius: (bottom - top) / 2,
      radiusX: (right - left) / 2,
      width: Math.max(this.measureText(parts.name.textContent ?? "", parts.name), this.measureText(parts.count.textContent ?? "", parts.count)) + 4,
      height: TERRITORY_TEXT_HEIGHT,
      importance: this.atlas.clusters.find((c) => c.id === id)?.size ?? 0,
    }));
    const obstacles: LabelObstacle[] =
      level === "clusters" ? shapes.map(({ id, box }) => ({ id, ...box })) : this.markBoxes();
    const placed = placeLabels(candidates, {
      budget: candidates.length,
      pinned: new Set(candidates.map((c) => c.id)),
      obstacles,
      sides: ["above", "below", "right", "left"],
      offset: 6,
      gap: 2,
      bounds: { x: 4, y: 4, width: viewport.width - 8, height: viewport.height - 8 },
    });
    this.territoryRects = placed.map((placement) => placement.rect);
    for (const placement of placed) {
      const shape = shapes.find((s) => s.id === placement.id);
      if (shape) this.setTerritoryText(shape.parts, placement.rect.x + placement.rect.width / 2, placement.rect.y + TERRITORY_NAME_HEIGHT / 2);
    }
  }

  /** The space each drawn mark takes, in screen pixels. */
  private markBoxes(): LabelObstacle[] {
    const boxes: LabelObstacle[] = [];
    for (const [id, node] of this.nodes) {
      const p = this.screen.get(id);
      if (!p || !this.isShown(id)) continue;
      const reach = node.radius * MARK_REACH;
      boxes.push({ id, x: p.x - reach, y: p.y - reach, width: 2 * reach, height: 2 * reach });
    }
    return boxes;
  }

  /** The name with its member count under it, both centred on `x`; `nameY` is the name's vertical centre. */
  private setTerritoryText(parts: TerritoryParts, x: number, nameY: number): void {
    parts.name.setAttribute("x", x.toFixed(1));
    parts.name.setAttribute("y", nameY.toFixed(1));
    parts.count.setAttribute("x", x.toFixed(1));
    parts.count.setAttribute("y", (nameY + TERRITORY_NAME_HEIGHT / 2 + 8).toFixed(1));
  }

  private clusterCentre(clusterId: string): Point | null {
    const cluster = this.atlas.clusters.find((c) => c.id === clusterId);
    if (!cluster) return null;
    const points = cluster.memberIds.filter((id) => this.isShown(id)).flatMap((id) => (this.screen.get(id) ? [this.screen.get(id) as Point] : []));
    return meanPoint(points);
  }

  private drawClusterLinks(level: DetailLevel): void {
    for (const link of this.links) {
      const a = this.clusterCentre(link.a);
      const b = this.clusterCentre(link.b);
      const visible = level === "clusters" && a !== null && b !== null;
      link.path.classList.toggle("is-off", !visible);
      // A single relationship is what a line already says; the figure is for links that carry more than one.
      link.label.classList.toggle("is-off", !visible || link.count < 2);
      if (!visible || !a || !b) continue;
      link.path.setAttribute("d", `M${a.x} ${a.y}L${b.x} ${b.y}`);
      link.label.setAttribute("x", String((a.x + b.x) / 2));
      link.label.setAttribute("y", String((a.y + b.y) / 2 - 5));
    }
  }

  private drawEdges(level: DetailLevel): void {
    const selectedId = this.selection?.kind === "entity" ? this.selection.id : null;
    const pathEdges = this.tracedPath?.edgeKeys ?? null;
    const activeEdges = pathEdges
      ? this.edges.filter((e) => pathEdges.has(e.key))
      : selectedId
        ? this.edges.filter((e) => e.sourceId === selectedId || e.targetId === selectedId)
        : [];
    const showEdgeLabels = level !== "clusters" && activeEdges.length > 0 && activeEdges.length <= MAX_EDGE_LABELS;
    this.edgeLabelQueue = [];
    for (const edge of this.edges) {
      const a = this.screen.get(edge.sourceId);
      const b = this.screen.get(edge.targetId);
      const ra = this.nodes.get(edge.sourceId)?.radius ?? 6;
      const rb = this.nodes.get(edge.targetId)?.radius ?? 6;
      const hidden = level === "clusters" || !a || !b || !this.isShown(edge.sourceId) || !this.isShown(edge.targetId);
      edge.path.classList.toggle("is-off", hidden);
      const isActive = showEdgeLabels && activeEdges.includes(edge);
      edge.label.classList.add("is-off"); // shown by `drawEdgeLabels` once it has found room
      if (hidden || !a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const length = Math.hypot(dx, dy);
      const gapA = ra * GLYPH_REACH * 0.8 + 3;
      const gapB = rb * GLYPH_REACH * 0.8 + 6;
      if (length < gapA + gapB + 6) {
        edge.path.classList.add("is-off");
        edge.label.classList.add("is-off");
        continue;
      }
      const ux = dx / length;
      const uy = dy / length;
      const x1 = a.x + ux * gapA;
      const y1 = a.y + uy * gapA;
      const x2 = b.x - ux * gapB;
      const y2 = b.y - uy * gapB;
      edge.path.setAttribute("d", `M${x1.toFixed(1)} ${y1.toFixed(1)}L${x2.toFixed(1)} ${y2.toFixed(1)}`);
      if (isActive) {
        const direction = pathEdges ? "out" : edge.sourceId === selectedId ? "out" : "in";
        const phrase = RELATION_PHRASES[edge.type][direction];
        edge.label.textContent = phrase;
        this.edgeLabelQueue.push({ edge, x1, y1, x2, y2, width: this.measureText(phrase, edge.label) + 2, height: EDGE_LABEL_HEIGHT });
      }
    }
  }

  /** After the entity names, so a phrase never lands on a name or a mark; one with no room stays hidden. */
  private drawEdgeLabels(viewport: Size): void {
    if (this.edgeLabelQueue.length === 0) return;
    const candidates: EdgeLabelCandidate[] = this.edgeLabelQueue.map((q) => ({ id: q.edge.key, x1: q.x1, y1: q.y1, x2: q.x2, y2: q.y2, width: q.width, height: q.height }));
    const placed = placeEdgeLabels(candidates, {
      blockers: this.blockers,
      bounds: { x: 4, y: 4, width: viewport.width - 8, height: viewport.height - 8 },
    });
    for (const { edge } of this.edgeLabelQueue) {
      const point = placed.get(edge.key);
      edge.label.classList.toggle("is-off", point === undefined);
      if (point) {
        edge.label.setAttribute("x", point.x.toFixed(1));
        edge.label.setAttribute("y", point.y.toFixed(1));
      }
    }
  }

  private drawNodes(): void {
    for (const [id, node] of this.nodes) {
      const p = this.screen.get(id);
      if (!p) continue;
      node.root.setAttribute("transform", `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`);
    }
  }

  /** Width of `text` set in the font of `sample` (a label element), cached per font. */
  private measureText(text: string, sample: SVGTextElement | undefined = this.nodes.values().next().value?.label): number {
    const key = `${sample?.getAttribute("class")?.split(" ")[0] ?? ""}|${this.svg.getAttribute("data-lod") ?? ""}|${text}`;
    const cached = this.measured.get(key);
    if (cached !== undefined) return cached;
    let width = text.length * 7.2;
    if (this.measureContext && sample) {
      const style = getComputedStyle(sample);
      this.measureContext.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      width = this.measureContext.measureText(text).width;
    }
    this.measured.set(key, width);
    return width;
  }

  private drawLabels(level: DetailLevel, viewport: Size): void {
    const pinned = new Set<string>(this.relatedIds());
    if (this.selection?.kind === "entity") pinned.add(this.selection.id);
    for (const id of this.tracedPath?.nodeIds ?? []) pinned.add(id);
    for (const id of this.filters.matches ?? []) pinned.add(id);
    if (this.hovered) pinned.add(this.hovered);

    const placements = new Map<string, LabelPlacement>();
    if (level !== "clusters") {
      const candidates: PlacedLabelCandidate[] = [];
      const obstacles: LabelObstacle[] = [];
      for (const [id, node] of this.nodes) {
        const p = this.screen.get(id);
        if (!p || !this.isShown(id)) continue;
        if (p.x < -40 || p.y < -40 || p.x > this.size.width + 40 || p.y > this.size.height + 40) continue;
        const reach = node.radius * MARK_REACH;
        obstacles.push({ id, x: p.x - reach, y: p.y - reach, width: 2 * reach, height: 2 * reach });
        const name = this.atlas.byId.get(id)?.name ?? "";
        candidates.push({
          id,
          x: p.x,
          y: p.y,
          radius: reach,
          width: this.measureText(name) + 2,
          height: LABEL_HEIGHT,
          importance: this.atlas.importance(id),
        });
      }
      // A small graph is meant to be read whole, so every name that finds room is drawn; the budget only
      // keeps a large graph from turning into noise.
      const budget = this.atlas.entities.length <= SMALL_GRAPH_NODES ? candidates.length : labelBudget(level, viewport);
      obstacles.push(...this.territoryRects);
      for (const placement of placeLabels(candidates, { budget, pinned, obstacles, bounds: { x: 4, y: 4, width: viewport.width - 8, height: viewport.height - 8 } })) {
        placements.set(placement.id, placement);
      }
      this.blockers = [...obstacles, ...[...placements.values()].map((placement) => placement.rect)];
    } else {
      this.blockers = [];
    }
    for (const [id, node] of this.nodes) {
      const placement = placements.get(id);
      node.label.classList.toggle("is-off", placement === undefined);
      if (placement) {
        node.label.setAttribute("x", placement.x.toFixed(1));
        node.label.setAttribute("y", placement.y.toFixed(1));
        node.label.setAttribute("text-anchor", placement.anchor);
      }
    }
  }

  // ---- input ----------------------------------------------------------------------------------

  private local(event: { clientX: number; clientY: number }): Point {
    const rect = this.svg.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  private onPointerDown(event: PointerEvent): void {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest(".node")) return; // a click on an entity is handled by `click`
    this.cancelAnimation();
    this.svg.setPointerCapture(event.pointerId);
    this.pointers.set(event.pointerId, this.local(event));
    if (this.pointers.size === 1) {
      this.gesture = { startedOn: target, startX: event.clientX, startY: event.clientY, moved: false, pinch: null };
    } else if (this.gesture) {
      this.gesture.pinch = this.pinchDistance();
      this.gesture.moved = true;
    }
  }

  private pinchDistance(): number | null {
    const [a, b] = [...this.pointers.values()];
    return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : null;
  }

  private onPointerMove(event: PointerEvent): void {
    const previous = this.pointers.get(event.pointerId);
    if (!previous || !this.gesture) return;
    const now = this.local(event);
    this.pointers.set(event.pointerId, now);
    const viewport = this.viewport();
    if (this.pointers.size >= 2) {
      const distance = this.pinchDistance();
      const [a, b] = [...this.pointers.values()];
      if (distance !== null && this.gesture.pinch && a && b) {
        this.camera = zoomAt(this.camera, distance / this.gesture.pinch, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, this.scale, viewport);
        this.gesture.pinch = distance;
        this.schedule();
      }
      return;
    }
    if (!this.gesture.moved && Math.hypot(event.clientX - this.gesture.startX, event.clientY - this.gesture.startY) < DRAG_THRESHOLD) return;
    this.gesture.moved = true;
    this.svg.classList.add("is-panning");
    this.camera = panBy(this.camera, now.x - previous.x, now.y - previous.y, this.scale);
    this.schedule();
  }

  private onPointerEnd(event: PointerEvent): void {
    if (!this.pointers.delete(event.pointerId)) return;
    this.svg.classList.remove("is-panning");
    const gesture = this.gesture;
    if (this.pointers.size > 0) return;
    this.gesture = null;
    if (!gesture || gesture.moved || event.type === "pointercancel") return;
    const territory = gesture.startedOn?.closest(".territory");
    const clusterId = territory?.getAttribute("data-cluster");
    this.onSelect(clusterId ? { kind: "cluster", id: clusterId } : null);
  }

  private onWheel(event: WheelEvent): void {
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1;
    const factor = Math.exp(-event.deltaY * unit * (event.ctrlKey ? 0.01 : 0.0015));
    this.cancelAnimation();
    this.camera = zoomAt(this.camera, factor, this.local(event), this.scale, this.viewport());
    this.schedule();
  }

  private onClick(event: MouseEvent): void {
    const node = event.target instanceof Element ? event.target.closest(".node") : null;
    const id = node?.getAttribute("data-id");
    if (id) this.onSelect({ kind: "entity", id });
  }

  private onHover(event: PointerEvent, entering: boolean): void {
    const node = event.target instanceof Element ? event.target.closest(".node") : null;
    const id = node?.getAttribute("data-id") ?? null;
    if (!id) return;
    const next = entering ? id : this.hovered === id ? null : this.hovered;
    if (next === this.hovered) return;
    this.hovered = next;
    for (const [nodeId, parts] of this.nodes) parts.root.classList.toggle("is-hovered", nodeId === next);
    this.schedule();
  }

  private onFocusIn(event: FocusEvent): void {
    const node = event.target instanceof Element ? event.target.closest(".node") : null;
    const id = node?.getAttribute("data-id");
    if (!id) return;
    this.tabStop = id;
    for (const [nodeId, parts] of this.nodes) parts.root.setAttribute("tabindex", nodeId === id ? "0" : "-1");
    this.ensureVisible(id);
  }

  /** Pan (never zoom) just far enough that a focused entity is not at the edge or under a panel. */
  private ensureVisible(id: string): void {
    const p = this.screen.get(id);
    const viewport = this.viewport();
    if (!p) return;
    const margin = 72;
    if (p.x >= margin && p.y >= margin && p.x <= viewport.width - margin && p.y <= viewport.height - margin) return;
    const position = this.layout.positions.get(id);
    if (position) this.moveTo({ ...this.camera, x: position.x, y: position.y }, true, 280);
  }

  private onKeyDown(event: KeyboardEvent): void {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target instanceof Element ? event.target : null;
    const node = target?.closest(".node") ?? null;
    const id = node?.getAttribute("data-id") ?? null;
    const arrows: Record<string, Direction> = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
    const direction = arrows[event.key];

    if (direction && id) {
      event.preventDefault();
      const next = nextInDirection(this.visibleNodePositions(), id, direction);
      if (next) this.focusNode(next);
      return;
    }
    if ((event.key === "Enter" || event.key === " ") && id) {
      event.preventDefault();
      this.onSelect({ kind: "entity", id });
      return;
    }
    if ((event.key === "Home" || event.key === "End") && id) {
      event.preventDefault();
      const order = readingOrder(this.visibleNodePositions());
      const wanted = event.key === "Home" ? order[0] : order[order.length - 1];
      if (wanted) this.focusNode(wanted);
      return;
    }
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
    if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      this.zoomBy(KEY_ZOOM);
    } else if (event.key === "-" || event.key === "_") {
      event.preventDefault();
      this.zoomBy(1 / KEY_ZOOM);
    } else if (event.key === "0") {
      event.preventDefault();
      this.fit(true);
    } else if (event.key === "Escape") {
      this.onSelect(null);
    } else if (direction && target === this.svg) {
      event.preventDefault();
      const dx = direction === "left" ? KEY_PAN : direction === "right" ? -KEY_PAN : 0;
      const dy = direction === "up" ? KEY_PAN : direction === "down" ? -KEY_PAN : 0;
      this.cancelAnimation();
      this.camera = panBy(this.camera, dx, dy, this.scale);
      this.schedule();
    }
  }

  // ---- small builders -------------------------------------------------------------------------

  private arrowMarker(id: string, headClass: string): SVGMarkerElement {
    return s(
      "marker",
      { id, viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: "auto" },
      s("path", { class: headClass, d: "M0.8 0.9L7 4L0.8 7.1", fill: "none" }),
    );
  }

  private controlButton(label: string, icon: "plus" | "minus" | "fit", onClick: () => void): HTMLButtonElement {
    const button = h("button", { type: "button", class: "control", "aria-label": label, title: label }, controlIcon(icon));
    button.addEventListener("click", onClick);
    return button;
  }
}
