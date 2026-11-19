/**
 * Node glyphs: one SVG path per shape in the design tokens.
 *
 * Colour says which of four groups an entity is in; shape says what kind it is (see
 * docs/FRONTEND.md, "Colour and shape"). Every path is centred on (0, 0) and drawn for a nominal
 * `radius`; the shapes are scaled so they carry about the same visual weight (equal area, roughly),
 * because a triangle drawn at the same circumradius as a circle looks far smaller.
 */
import type { ShapeKey } from "../../ui/src/tokens.ts";

type Vertex = readonly [x: number, y: number];

const round = (n: number): string => {
  const r = Math.round(n * 100) / 100;
  return String(Object.is(r, -0) ? 0 : r);
};

function polygon(vertices: ReadonlyArray<Vertex>): string {
  return `${vertices.map(([x, y], i) => `${i === 0 ? "M" : "L"}${round(x)} ${round(y)}`).join("")}Z`;
}

/** Vertices of a regular polygon; `rotation` 0 puts the first vertex straight up. */
function regular(sides: number, circumradius: number, rotation = 0): Vertex[] {
  const out: Vertex[] = [];
  for (let i = 0; i < sides; i += 1) {
    const angle = rotation + (i / sides) * 2 * Math.PI;
    out.push([Math.sin(angle) * circumradius, -Math.cos(angle) * circumradius]);
  }
  return out;
}

function star(points: number, outer: number, inner: number): Vertex[] {
  const out: Vertex[] = [];
  for (let i = 0; i < points * 2; i += 1) {
    const radius = i % 2 === 0 ? outer : inner;
    const angle = (i / (points * 2)) * 2 * Math.PI;
    out.push([Math.sin(angle) * radius, -Math.cos(angle) * radius]);
  }
  return out;
}

function plus(arm: number, thickness: number): Vertex[] {
  const a = arm;
  const t = thickness;
  return [
    [-t, -a], [t, -a], [t, -t], [a, -t], [a, t], [t, t],
    [t, a], [-t, a], [-t, t], [-a, t], [-a, -t], [-t, -t],
  ]; // prettier-ignore
}

/** Total over `ShapeKey`: a new shape in the tokens fails the type check until it is drawn here. */
const BUILDERS: Readonly<Record<ShapeKey, (r: number) => string>> = {
  circle: (r) => `M${round(r)} 0A${round(r)} ${round(r)} 0 1 0 ${round(-r)} 0A${round(r)} ${round(r)} 0 1 0 ${round(r)} 0Z`,
  square: (r) => polygon([[-0.9 * r, -0.9 * r], [0.9 * r, -0.9 * r], [0.9 * r, 0.9 * r], [-0.9 * r, 0.9 * r]]),
  diamond: (r) => polygon(regular(4, 1.3 * r)),
  triangle: (r) => polygon(regular(3, 1.5 * r)),
  "triangle-down": (r) => polygon(regular(3, 1.5 * r, Math.PI)),
  hexagon: (r) => polygon(regular(6, 1.1 * r, Math.PI / 6)),
  plus: (r) => polygon(plus(1.2 * r, 0.42 * r)),
  star: (r) => polygon(star(5, 1.45 * r, 0.725 * r)),
};

/** An SVG path `d` for `shape`, centred on the origin, sized for `radius` (a finite number above 0). */
export function glyphPath(shape: ShapeKey, radius: number): string {
  if (!Number.isFinite(radius) || radius <= 0) throw new RangeError("radius must be a positive finite number");
  return BUILDERS[shape](radius);
}

/** How far from the centre a glyph can reach, as a multiple of its radius. Use it for hit areas and clearance. */
export const GLYPH_REACH = 1.5;
