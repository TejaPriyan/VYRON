/** Small drawn marks shared by the panels: entity glyphs, line samples and provenance badges. */
import { glyphPath } from "../../../../packages/graph/src/glyph.ts";
import type { EntityKind, Provenance } from "../../../../packages/types/src/api.ts";
import { kindVisual, provenanceVisual, tokens } from "../../../../packages/ui/src/tokens.ts";
import type { ProvenanceKey } from "../../../../packages/ui/src/tokens.ts";
import { h, s } from "./dom.ts";
import { provenanceIcon } from "./icons.ts";

/** Total over the provenance labels: what each one means for a reader, in one plain sentence. */
export const PROVENANCE_HELP: Readonly<Record<ProvenanceKey, string>> = {
  sourced: "Backed by at least one recorded source.",
  estimated: "Calculated by VYRON from other data.",
  inferred: "Worked out from other facts, not stated by a source.",
  predicted: "A forecast, not something that has happened.",
  ai_generated: "Written by a model and not checked against a source.",
  user_generated: "Added by you.",
  seed: "Development data for trying the map. Not a verified fact.",
};

export function kindGlyph(kind: EntityKind, size = 14): SVGSVGElement {
  const visual = kindVisual(kind);
  return s(
    "svg",
    { class: "kind-glyph", width: size, height: size, viewBox: "-9 -9 18 18", "aria-hidden": "true", focusable: "false" },
    s("path", { d: glyphPath(visual.shape, 6), fill: visual.color }),
  );
}

export function groupSwatch(group: keyof typeof tokens.groups): SVGSVGElement {
  return s(
    "svg",
    { class: "kind-glyph", width: 10, height: 10, viewBox: "0 0 10 10", "aria-hidden": "true", focusable: "false" },
    s("circle", { cx: 5, cy: 5, r: 4.5, fill: tokens.groups[group].color }),
  );
}

/** A short horizontal line drawn with the same dash pattern the map uses for a provenance style. */
export function lineSample(dash: string, width = 40): SVGSVGElement {
  return s(
    "svg",
    { class: "line-sample", width, height: 8, viewBox: `0 0 ${width} 8`, "aria-hidden": "true", focusable: "false" },
    s("path", { d: `M1 4H${width - 1}`, fill: "none", stroke: "currentColor", "stroke-width": 1.4, ...(dash === "none" || dash === "" ? {} : { "stroke-dasharray": dash }) }),
  );
}

/** Icon and words together, so provenance is never carried by colour or an icon alone. */
export function provenanceBadge(provenance: Provenance): HTMLElement {
  const visual = provenanceVisual(provenance);
  return h("span", { class: "badge", "data-provenance": provenance, "data-style": visual.styleClass, title: PROVENANCE_HELP[provenance] }, provenanceIcon(visual.icon, 14), h("span", {}, visual.label));
}
