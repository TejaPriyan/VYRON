import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  boundsOf, centreOn, easeOutCubic, ENTITY_SPAN_PX, fitScale, initialZoom, lerpCamera, panBy, screenToWorld,
  SMALL_GRAPH_NODES, worldToScreen, zoomAt, zoomToFit,
} from "./camera.ts";
import { detailLevel, LOD } from "./lod.ts";

const viewport = { width: 1000, height: 700 };
const near = (a: number, b: number, tolerance = 1e-6): void => assert.ok(Math.abs(a - b) <= tolerance, `${a} is not within ${tolerance} of ${b}`);

describe("boundsOf", () => {
  it("wraps the points, with padding, and ignores points that are not numbers", () => {
    assert.deepEqual(boundsOf([{ x: 1, y: 2 }, { x: 5, y: 4 }, { x: Number.NaN, y: 0 }]), { x: 1, y: 2, width: 4, height: 2 });
    assert.deepEqual(boundsOf([{ x: 0, y: 0 }], 2), { x: -2, y: -2, width: 4, height: 4 });
    assert.equal(boundsOf([]), null);
  });
});

describe("opening view", () => {
  const bounds = { x: -6, y: -5, width: 12, height: 10 };
  const scale = fitScale(bounds, viewport, 48);

  it("fits the whole graph inside the padding when it opens at entity level", () => {
    const camera = centreOn(bounds, initialZoom(17));
    for (const corner of [{ x: -6, y: -5 }, { x: 6, y: -5 }, { x: 6, y: 5 }, { x: -6, y: 5 }]) {
      const s = worldToScreen(camera, scale, viewport, corner);
      assert.ok(s.x >= 48 - 1e-6 && s.x <= viewport.width - 48 + 1e-6, `x ${s.x}`);
      assert.ok(s.y >= 48 - 1e-6 && s.y <= viewport.height - 48 + 1e-6, `y ${s.y}`);
    }
  });

  it("shows entities for a small graph and cluster summaries for a large one", () => {
    assert.equal(detailLevel(initialZoom(17)), "nodes");
    assert.equal(detailLevel(initialZoom(SMALL_GRAPH_NODES)), "nodes");
    assert.equal(detailLevel(initialZoom(SMALL_GRAPH_NODES + 1)), "clusters");
    assert.equal(initialZoom(17), LOD.nodesFromZoom);
  });

  it("opens a small graph at entity level only when the viewport has room for it", () => {
    const need = ENTITY_SPAN_PX * Math.sqrt(SMALL_GRAPH_NODES);
    assert.equal(detailLevel(initialZoom(SMALL_GRAPH_NODES, { width: 780, height: 816 })), "nodes");
    assert.equal(detailLevel(initialZoom(SMALL_GRAPH_NODES, { width: need, height: need })), "nodes");
    assert.equal(detailLevel(initialZoom(SMALL_GRAPH_NODES, { width: need - 1, height: 900 })), "clusters");
    assert.equal(detailLevel(initialZoom(SMALL_GRAPH_NODES, { width: 390, height: 700 })), "clusters");
    assert.equal(detailLevel(initialZoom(SMALL_GRAPH_NODES, { width: 1200, height: 380 })), "clusters");
  });

  it("asks less of the viewport for a smaller graph", () => {
    assert.equal(detailLevel(initialZoom(9, { width: 300, height: 520 })), "nodes");
    assert.equal(detailLevel(initialZoom(17, { width: 300, height: 520 })), "clusters");
  });

  it("falls back to the entity count when the viewport has no size yet", () => {
    assert.equal(detailLevel(initialZoom(SMALL_GRAPH_NODES, { width: 0, height: 0 })), "nodes");
    assert.equal(detailLevel(initialZoom(SMALL_GRAPH_NODES + 1, { width: 2000, height: 2000 })), "clusters");
  });

  it("collapses to clusters one notch out and to labels when zoomed in", () => {
    const camera = centreOn(bounds, initialZoom(17));
    const out = zoomAt(camera, 0.8, { x: 500, y: 350 }, scale, viewport);
    assert.equal(detailLevel(out.zoom), "clusters");
    const inn = zoomAt(camera, 2.2, { x: 500, y: 350 }, scale, viewport);
    assert.equal(detailLevel(inn.zoom), "labelled");
  });
});

describe("conversions", () => {
  const camera = { x: 3, y: -2, zoom: 1.7 };
  const scale = 40;

  it("round-trip between world and screen", () => {
    for (const p of [{ x: 0, y: 0 }, { x: -4.5, y: 9 }, { x: 100, y: -100 }]) {
      const back = screenToWorld(camera, scale, viewport, worldToScreen(camera, scale, viewport, p));
      near(back.x, p.x);
      near(back.y, p.y);
    }
  });

  it("puts the camera point at the centre of the viewport", () => {
    const s = worldToScreen(camera, scale, viewport, { x: camera.x, y: camera.y });
    assert.deepEqual(s, { x: 500, y: 350 });
  });
});

describe("zoomAt", () => {
  const scale = 40;

  it("keeps the world point under the pointer where it is", () => {
    const camera = { x: 1, y: 1, zoom: 1.4 };
    for (const anchor of [{ x: 100, y: 80 }, { x: 900, y: 600 }, { x: 500, y: 350 }]) {
      const before = screenToWorld(camera, scale, viewport, anchor);
      const zoomed = zoomAt(camera, 1.6, anchor, scale, viewport);
      const after = screenToWorld(zoomed, scale, viewport, anchor);
      near(after.x, before.x);
      near(after.y, before.y);
      near(zoomed.zoom, 1.4 * 1.6);
    }
  });

  it("stops at the zoom limits and returns the same camera there", () => {
    const top = { x: 0, y: 0, zoom: LOD.maxZoom };
    assert.equal(zoomAt(top, 2, { x: 1, y: 1 }, scale, viewport), top);
    const clamped = zoomAt({ x: 0, y: 0, zoom: 1 }, 1e6, { x: 1, y: 1 }, scale, viewport);
    assert.equal(clamped.zoom, LOD.maxZoom);
    const bottom = zoomAt({ x: 0, y: 0, zoom: 1 }, 1e-6, { x: 1, y: 1 }, scale, viewport);
    assert.equal(bottom.zoom, LOD.minZoom);
  });

  it("ignores a factor that is not a positive number", () => {
    const camera = { x: 0, y: 0, zoom: 1 };
    for (const factor of [0, -2, Number.NaN, Number.POSITIVE_INFINITY]) assert.equal(zoomAt(camera, factor, { x: 0, y: 0 }, scale, viewport), camera);
  });
});

describe("panBy", () => {
  it("makes the map follow the pointer", () => {
    const camera = { x: 2, y: 3, zoom: 2 };
    const scale = 30;
    const grabbed = { x: 400, y: 300 };
    const world = screenToWorld(camera, scale, viewport, grabbed);
    const moved = panBy(camera, 55, -35, scale);
    const now = worldToScreen(moved, scale, viewport, world);
    near(now.x, grabbed.x + 55);
    near(now.y, grabbed.y - 35);
  });
});

describe("transitions", () => {
  it("eases from 0 to 1 and clamps", () => {
    assert.equal(easeOutCubic(0), 0);
    assert.equal(easeOutCubic(1), 1);
    assert.equal(easeOutCubic(-3), 0);
    assert.equal(easeOutCubic(9), 1);
    assert.ok(easeOutCubic(0.5) > 0.5, "starts fast");
  });

  it("moves zoom in log space between two cameras", () => {
    const a = { x: 0, y: 0, zoom: 1 };
    const b = { x: 10, y: -4, zoom: 4 };
    assert.deepEqual(lerpCamera(a, b, 0), a);
    const end = lerpCamera(a, b, 1);
    near(end.x, 10);
    near(end.zoom, 4);
    const mid = lerpCamera(a, b, 0.5);
    near(mid.x, 5);
    near(mid.y, -2);
    near(mid.zoom, 2);
  });
});

describe("zoomToFit", () => {
  it("fills the viewport with the given rectangle", () => {
    const scale = 40;
    const rect = { x: 0, y: 0, width: 6, height: 4 };
    const zoom = zoomToFit(rect, scale, viewport, 48);
    const k = zoom * scale; // the tighter dimension exactly fills the space inside the padding
    near(Math.max((6 * k) / (1000 - 96), (4 * k) / (700 - 96)), 1, 1e-9);
    assert.ok(Number.isFinite(zoom) && zoom >= LOD.minZoom && zoom <= LOD.maxZoom);
    const camera = centreOn(rect, zoom);
    const corner = worldToScreen(camera, scale, viewport, { x: 0, y: 0 });
    assert.ok(corner.x >= 48 - 1e-6 && corner.y >= 48 - 1e-6);
  });

  it("is limited by the zoom range for a tiny or a huge rectangle", () => {
    assert.equal(zoomToFit({ x: 0, y: 0, width: 1e-9, height: 1e-9 }, 1, viewport), LOD.maxZoom);
    assert.equal(zoomToFit({ x: 0, y: 0, width: 1e9, height: 1e9 }, 1, viewport), LOD.minZoom);
  });
});
