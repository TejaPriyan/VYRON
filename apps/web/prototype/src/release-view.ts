/**
 * The release history of one package, drawn from what the API returned. Everything is built as text and SVG
 * (never markup). The strip is a picture; the table beneath it lists every release in words, and the line under the
 * strip says in full what the selected mark is. A mark is a circle for an ordinary release, hollow for a
 * pre-release by its version number, and a diamond for a yanked or deprecated one, so no state rests on colour.
 *
 * Marks are small (the drawing can hold hundreds), so the strip is one tab stop: Tab reaches the selected mark and
 * the arrow keys move between releases. Two buttons do the same, and they are the larger targets for a pointer.
 */
import {
  DEFAULT_TIMELINE_METRICS,
  layoutReleases,
  stepRelease,
  widthForYears,
} from "../../../../packages/graph/src/release-timeline.ts";
import type { TimelineLayout } from "../../../../packages/graph/src/release-timeline.ts";
import type { PackageRegistry } from "../../../../packages/graph/src/dependency-graph.ts";
import type { PackageReleases } from "../../../../packages/types/src/api.ts";
import { describePackageCache, isStandIn, REGISTRY_NAMES, STAND_IN_NOTE } from "./dependencies-model.ts";
import type { PackageFailure } from "./dependencies-model.ts";
import { clear, h, s } from "./dom.ts";
import { provenanceBadge } from "./marks.ts";
import {
  describeCrowding,
  describeDateMeaning,
  describeLoadButton,
  describeLoadLead,
  describeLoading,
  describeOmitted,
  describePosition,
  describeRelease,
  describeReleaseLeftOut,
  describeShowAll,
  describeStrip,
  describeUnreadableHistory,
  MARK_RULES,
  NOT_A_JUDGEMENT,
  RELEASE_LEGEND,
  releaseLabel,
  releaseRows,
  summarizeReleases,
} from "./releases-model.ts";

export type ReleasePhase = "idle" | "loading" | "failed" | "loaded";

/** What is known about one package's release history, and the choices made on it (which release, the table). */
export interface ReleaseState {
  phase: ReleasePhase;
  data: PackageReleases | null;
  failure: PackageFailure | null;
  /** Index into `data.releases` (oldest first). */
  selected: number;
  showAll: boolean;
  tableOpen: boolean;
}

export const newReleaseState = (): ReleaseState => ({ phase: "idle", data: null, failure: null, selected: -1, showAll: false, tableOpen: false });

export interface ReleaseTarget {
  readonly registry: PackageRegistry;
  readonly name: string;
  /** False when the package is a link, path or alias that no registry could be asked about by name. */
  readonly readable: boolean;
}

export interface ReleaseHandlers {
  /** Ask for the history (the button, and "Try again"). */
  load(): void;
  /** The view changed something that needs the whole section built again (the table's size). */
  rerender(): void;
  /** Say something through the page's live region. */
  announce(message: string): void;
}

export interface ReleaseView {
  /** Update the "how old" line in place. */
  paintAge(): void;
}

const NO_VIEW: ReleaseView = { paintAge: () => undefined };

function failureBlock(failure: PackageFailure, retry: (() => void) | null): HTMLElement {
  const again = retry && failure.retryable ? h("button", { type: "button", class: "text-button", id: "release-retry" }, "Try again") : null;
  if (again && retry) again.addEventListener("click", retry);
  return h(
    "div",
    { class: "failure", role: "alert" },
    h("p", { class: "failure-title" }, failure.title),
    failure.body ? h("p", { class: "failure-body" }, failure.body) : null,
    failure.wait ? h("p", { class: "dust" }, failure.wait) : null,
    failure.requestId ? h("p", { class: "dust small" }, `Request ${failure.requestId}.`) : null,
    again,
  );
}

function legend(): HTMLElement {
  return h(
    "ul",
    { class: "graph-legend", "aria-label": "What the marks mean" },
    ...RELEASE_LEGEND.map((item) =>
      h(
        "li",
        { class: "legend-item" },
        s(
          "svg",
          { class: "legend-mark", width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": "true", focusable: "false" },
          s("path", { class: "release-shape", "data-hollow": item.hollow ? "true" : "false", d: shapePath(item.shape, 7, 7, 4) }),
        ),
        h("span", {}, item.text),
      ),
    ),
  );
}

/** The outline of a mark centred at (cx, cy): a circle, or a diamond a little larger so it looks as heavy. */
function shapePath(shape: "circle" | "diamond", cx: number, cy: number, r: number): string {
  if (shape === "circle") return `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0Z`;
  const d = r * 1.3;
  return `M${cx} ${cy - d}L${cx + d} ${cy}L${cx} ${cy + d}L${cx - d} ${cy}Z`;
}

interface Strip {
  readonly svg: SVGSVGElement;
  readonly scroll: HTMLElement;
  readonly layout: TimelineLayout;
}

function drawStrip(name: string, data: PackageReleases, layout: TimelineLayout, state: ReleaseState, choose: (index: number, focus: boolean) => void): Strip {
  const { markSize } = DEFAULT_TIMELINE_METRICS;
  const releases = data.releases;
  const ticks = layout.ticks.flatMap((tick) => [
    s("line", { class: "release-tick", x1: tick.x, x2: tick.x, y1: layout.axisY, y2: layout.axisY + 5 }),
    s("text", { class: "release-tick-label", x: tick.x, y: layout.axisY + 19, "text-anchor": "middle" }, String(tick.year)),
  ]);
  const marks = layout.marks.map((mark) => {
    const release = releases[mark.index];
    const group = s(
      "g",
      {
        class: "release-mark",
        transform: `translate(${mark.x} ${mark.y})`,
        role: "button",
        tabindex: mark.index === state.selected ? 0 : -1,
        "aria-label": release ? releaseLabel(release) : mark.version,
        "data-index": mark.index,
        "data-hollow": mark.hollow ? "true" : "false",
        "data-shape": mark.shape,
        "data-status": release?.status ?? "normal",
      },
      s("circle", { class: "release-hit", r: 10 }),
      s("path", { class: "release-shape", "data-hollow": mark.hollow ? "true" : "false", d: shapePath(mark.shape, 0, 0, markSize / 2) }),
    );
    group.addEventListener("click", () => choose(mark.index, true));
    return group;
  });
  const cursor = s("line", { class: "release-cursor", x1: 0, x2: 0, y1: 0, y2: layout.axisY });
  const svg = s(
    "svg",
    {
      class: "release-svg",
      width: layout.width,
      height: layout.height,
      viewBox: `0 0 ${layout.width} ${layout.height}`,
      role: "group",
      "aria-label": describeStrip(name, releases),
    },
    s("line", { class: "release-axis", x1: 0, x2: layout.width, y1: layout.axisY, y2: layout.axisY }),
    ...ticks,
    cursor,
    s("g", { class: "release-marks" }, ...marks),
  );
  svg.addEventListener("keydown", (event) => {
    const here = (event.target as Element | null)?.closest<SVGElement>("[data-index]")?.getAttribute("data-index");
    if (here === null || here === undefined) return;
    const next = stepRelease(releases.length, Number(here), event.key);
    if (next !== null) {
      event.preventDefault();
      choose(next, true);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(Number(here), true);
    }
  });
  const scroll = h("div", { class: "release-scroll" }, svg);
  return { svg, scroll, layout };
}

function table(name: string, data: PackageReleases, state: ReleaseState, handlers: ReleaseHandlers): HTMLElement {
  const { rows } = releaseRows(data.releases, state.showAll);
  const body = h(
    "tbody",
    {},
    ...rows.map((row) =>
      h(
        "tr",
        { class: "release-row", "data-index": row.index },
        h("th", { scope: "row", class: "release-version" }, row.version),
        h("td", {}, row.date),
        h("td", {}, row.notes.join(", ")),
      ),
    ),
  );
  const label = describeShowAll(data.releases.length, state.showAll);
  const more = label
    ? h("button", { type: "button", class: "text-button release-more", id: "release-more" }, label)
    : null;
  more?.addEventListener("click", () => {
    state.showAll = !state.showAll;
    handlers.rerender();
  });
  const details = h(
    "details",
    { class: "dep-outline release-list", ...(state.tableOpen ? { open: true } : {}) },
    h("summary", {}, "Show every release as a table"),
    h(
      "div",
      { class: "release-table-wrap" },
      h(
        "table",
        { class: "release-table" },
        h("caption", { class: "sr-only" }, `Every release of ${name} in the list, newest first`),
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Version"), h("th", { scope: "col" }, "Date (UTC)"), h("th", { scope: "col" }, "Notes"))),
        body,
      ),
      more,
    ),
  );
  details.addEventListener("toggle", () => {
    state.tableOpen = details.open;
  });
  return details;
}

function loaded(name: string, data: PackageReleases, state: ReleaseState, handlers: ReleaseHandlers, now: () => number): { body: HTMLElement; view: ReleaseView } {
  const releases = data.releases;
  const summary = summarizeReleases(releases);
  const layout = layoutReleases(releases, { ...DEFAULT_TIMELINE_METRICS, width: widthForYears(releases.length === 0 ? 1 : yearsSpanned(releases)) });
  const cache = describePackageCache(data, now());
  const ageText = h("span", { class: "age", "aria-live": "off" }, cache.text);
  const cacheLabel = h("span", { class: "cache-label" }, cache.label);
  const band = h("p", { class: "band-line", "data-tone": cache.tone }, provenanceBadge(data.provenance), cacheLabel, ageText);
  const view: ReleaseView = {
    paintAge: () => {
      const fresh = describePackageCache(data, now());
      cacheLabel.textContent = fresh.label;
      ageText.textContent = fresh.text;
      band.setAttribute("data-tone", fresh.tone);
    },
  };
  const notes = [describeOmitted(data.omitted_older), layout ? describeCrowding(layout.crowded) : null].filter((text): text is string => text !== null);
  const leftOut = describeReleaseLeftOut(data.left_out);
  const common = [
    band,
    isStandIn(data) ? h("p", { class: "stand-in" }, STAND_IN_NOTE) : null,
    h("p", { class: "release-summary" }, summary.text),
    h("p", { class: "note" }, describeDateMeaning(data.date_meaning, data.registry)),
    leftOut.length > 0 ? h("ul", { class: "left-out" }, ...leftOut.map((text) => h("li", {}, text))) : null,
    ...notes.map((text) => h("p", { class: "note" }, text)),
  ];
  if (!layout) return { body: h("div", { class: "release-body" }, ...common), view };

  const position = h("span", { class: "release-position" });
  const readout = h("p", { class: "release-readout", id: "release-readout" });
  const earlier = h("button", { type: "button", class: "release-step", id: "release-earlier" }, "Earlier release");
  const later = h("button", { type: "button", class: "release-step", id: "release-later" }, "Later release");
  const cursorOf = (): SVGLineElement => strip.svg.querySelector<SVGLineElement>(".release-cursor") as SVGLineElement;
  const markFor = (index: number): SVGElement | null => strip.svg.querySelector<SVGElement>(`.release-mark[data-index="${index}"]`);

  const paint = (focus: boolean): void => {
    const index = state.selected;
    const release = releases[index];
    for (const mark of strip.svg.querySelectorAll<SVGElement>(".release-mark")) {
      const mine = Number(mark.getAttribute("data-index")) === index;
      mark.classList.toggle("is-selected", mine);
      mark.setAttribute("tabindex", mine ? "0" : "-1");
      if (mine) mark.setAttribute("aria-current", "true");
      else mark.removeAttribute("aria-current");
    }
    const placed = layout.marks.find((mark) => mark.index === index);
    const cursor = cursorOf();
    if (placed) {
      cursor.setAttribute("x1", String(placed.x));
      cursor.setAttribute("x2", String(placed.x));
      cursor.setAttribute("y2", String(placed.y));
      cursor.classList.add("is-shown");
    } else cursor.classList.remove("is-shown");
    position.textContent = describePosition(index, releases.length);
    readout.textContent = release ? describeRelease(release) : "";
    earlier.setAttribute("aria-disabled", index <= 0 ? "true" : "false");
    later.setAttribute("aria-disabled", index >= releases.length - 1 ? "true" : "false");
    for (const row of body.querySelectorAll<HTMLElement>(".release-row")) {
      const mine = Number(row.getAttribute("data-index")) === index;
      row.classList.toggle("is-current", mine);
      if (mine) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    }
    const mark = markFor(index);
    if (focus) mark?.focus({ preventScroll: true });
    mark?.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  const choose = (index: number, focus: boolean): void => {
    if (index < 0 || index >= releases.length) return;
    state.selected = index;
    paint(focus);
  };
  const stepBy = (button: HTMLElement, key: "ArrowLeft" | "ArrowRight"): void => {
    button.addEventListener("click", () => {
      const next = stepRelease(releases.length, state.selected, key);
      if (next === null) return;
      choose(next, false);
      const release = releases[next];
      if (release) handlers.announce(`${describePosition(next, releases.length)}. ${describeRelease(release)}`);
    });
  };
  const strip = drawStrip(name, data, layout, state, choose);
  stepBy(earlier, "ArrowLeft");
  stepBy(later, "ArrowRight");

  const body = h(
    "div",
    { class: "release-body" },
    ...common,
    legend(),
    strip.scroll,
    h("div", { class: "release-controls" }, earlier, position, later),
    readout,
    h("p", { class: "dust small" }, MARK_RULES),
    table(name, data, state, handlers),
    h("p", { class: "note" }, NOT_A_JUDGEMENT),
  );
  paint(false);
  return { body, view };
}

function yearsSpanned(releases: PackageReleases["releases"]): number {
  const first = releases[0];
  const last = releases[releases.length - 1];
  if (!first || !last) return 1;
  return new Date(last.released_at).getUTCFullYear() - new Date(first.released_at).getUTCFullYear() + 1;
}

/**
 * Build the section's content for the package's current state, replacing what `host` holds. Keyboard focus is kept
 * where it was when the control it was on still exists; otherwise the caller decides where it goes.
 */
export function renderReleases(host: HTMLElement, target: ReleaseTarget, state: ReleaseState, handlers: ReleaseHandlers, now: () => number = Date.now): ReleaseView {
  const active = document.activeElement;
  const hadFocus = active instanceof Element && host.contains(active);
  const focusedId = hadFocus ? (active as Element).id : "";
  const focusedMark = hadFocus ? (active as Element).closest(".release-mark")?.getAttribute("data-index") : null;
  clear(host);

  let view: ReleaseView = NO_VIEW;
  const where = REGISTRY_NAMES[target.registry];
  if (!target.readable) {
    host.append(h("p", { class: "dust" }, describeUnreadableHistory(target.name)));
  } else if (state.phase === "loading") {
    host.append(h("p", { class: "state-line", role: "status" }, describeLoading(target.name)));
  } else if (state.phase === "failed" && state.failure) {
    host.append(failureBlock(state.failure, handlers.load));
  } else if (state.phase === "loaded" && state.data) {
    const built = loaded(target.name, state.data, state, handlers, now);
    host.append(
      h("p", { class: "release-for" }, h("span", { class: "tag" }, where), ` ${target.name}`),
      built.body,
    );
    view = built.view;
  } else {
    const button = h("button", { type: "button", class: "primary", id: "release-load" }, describeLoadButton(target.name));
    button.addEventListener("click", handlers.load);
    host.append(h("p", { class: "release-lede" }, describeLoadLead(target.name, target.registry)), h("p", { class: "details-actions" }, button));
  }

  if (focusedId) host.querySelector<HTMLElement>(`#${focusedId}`)?.focus({ preventScroll: true });
  else if (focusedMark !== null && focusedMark !== undefined) host.querySelector<SVGElement>(`.release-mark[data-index="${focusedMark}"]`)?.focus({ preventScroll: true });
  return view;
}
