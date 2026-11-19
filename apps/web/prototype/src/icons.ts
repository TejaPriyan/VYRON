/**
 * Icons for provenance labels, the brand mark and the map controls. Drawn with strokes on a 16 x 16
 * grid so they follow the text colour. The provenance icon names come from the design tokens
 * (`tokens.provenance[*].icon`); the table is total over them so a new name cannot ship undrawn.
 */
import { tokens } from "../../../../packages/ui/src/tokens.ts";
import { s } from "./dom.ts";

export type ProvenanceIconName = (typeof tokens.provenance)[keyof typeof tokens.provenance]["icon"];

const PATHS: Readonly<Record<ProvenanceIconName, ReadonlyArray<string>>> = {
  "seal-check": ["M8 1.8l1.6 1.1 1.9-.1.9 1.7 1.6 1-.4 1.9.4 1.9-1.6 1-.9 1.7-1.9-.1L8 14.2l-1.6-1.1-1.9.1-.9-1.7-1.6-1 .4-1.9-.4-1.9 1.6-1 .9-1.7 1.9.1z", "M5.4 8.2l1.8 1.8 3.4-3.6"],
  approx: ["M2.5 6c1.2-1.4 2.4-1.4 3.6 0s2.4 1.4 3.6 0 2.4-1.4 3.8 0", "M2.5 10.5c1.2-1.4 2.4-1.4 3.6 0s2.4 1.4 3.6 0 2.4-1.4 3.8 0"],
  branch: ["M4.5 2.5v11", "M4.5 9.5c0-2.5 7-1.5 7-5.5", "M11.5 2.8v2.4"],
  forecast: ["M2.5 12.5l3.5-3.5 2.5 2 5-6", "M10 5h3.5v3.5"],
  spark: ["M8 1.5l1.4 4.1 4.1 1.4-4.1 1.4L8 12.5 6.6 8.4 2.5 7l4.1-1.4z"],
  person: ["M8 7.5a2.7 2.7 0 100-5.4 2.7 2.7 0 000 5.4z", "M2.8 14c.4-2.8 2.5-4.2 5.2-4.2s4.8 1.4 5.2 4.2"],
  beaker: ["M6.2 2h3.6", "M6.8 2v4.2L3.2 12.6c-.5.9.1 1.9 1.1 1.9h7.4c1 0 1.6-1 1.1-1.9L9.2 6.2V2", "M4.6 10.5h6.8"],
};

export function provenanceIcon(name: ProvenanceIconName, size = 16): SVGSVGElement {
  return s(
    "svg",
    { class: "icon", width: size, height: size, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 1.3, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" },
    ...PATHS[name].map((d) => s("path", { d })),
  );
}

const CONTROL_PATHS = {
  plus: ["M8 3.5v9", "M3.5 8h9"],
  minus: ["M3.5 8h9"],
  fit: ["M2.5 6V2.5H6", "M10 2.5h3.5V6", "M13.5 10v3.5H10", "M6 13.5H2.5V10"],
  close: ["M3.5 3.5l9 9", "M12.5 3.5l-9 9"],
  layers: ["M8 2.5l5.5 3-5.5 3-5.5-3z", "M2.5 8.5L8 11.5l5.5-3", "M2.5 11L8 14l5.5-3"],
  search: ["M7 11.5a4.5 4.5 0 100-9 4.5 4.5 0 000 9z", "M10.4 10.4l3.1 3.1"],
} as const;

export type ControlIconName = keyof typeof CONTROL_PATHS;

export function controlIcon(name: ControlIconName, size = 16): SVGSVGElement {
  return s(
    "svg",
    { class: "icon", width: size, height: size, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 1.4, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" },
    ...CONTROL_PATHS[name].map((d) => s("path", { d })),
  );
}

/** Three linked points in a ring, one of them in the accent colour: the whole product in 20 pixels. */
export function brandMark(): SVGSVGElement {
  return s(
    "svg",
    { class: "mark", width: 22, height: 22, viewBox: "0 0 22 22", fill: "none", "aria-hidden": "true", focusable: "false" },
    s("circle", { cx: 11, cy: 11, r: 9.6, stroke: "currentColor", "stroke-width": 1, opacity: 0.55, "stroke-dasharray": "8 3 1 3" }),
    s("path", { d: "M6.2 13.6L11 6.4l4.8 7.2z", stroke: "currentColor", "stroke-width": 1, "stroke-linejoin": "round", opacity: 0.8 }),
    s("circle", { cx: 6.2, cy: 13.6, r: 1.7, fill: "currentColor" }),
    s("circle", { cx: 15.8, cy: 13.6, r: 1.7, fill: "currentColor" }),
    s("circle", { cx: 11, cy: 6.4, r: 1.9, class: "mark-signal" }),
  );
}
