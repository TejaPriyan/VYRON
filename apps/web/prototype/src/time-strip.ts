/**
 * The timeline strip under the map: a year slider over a row of marks, one for each entity that has a date.
 * Moving the slider tells the map which entities to leave out as of that year. The words come from
 * `time-model.ts`; this file draws them and reports what the reader does.
 *
 * The slider is a native range input, so the keyboard, touch and screen readers work without any code of ours.
 * The marks are decoration for sighted readers (they repeat what the slider and the list of names say).
 */
import type { Atlas } from "../../../../packages/graph/src/atlas.ts";
import { glyphPath } from "../../../../packages/graph/src/glyph.ts";
import { axisTicks, clampYear, yearToX } from "../../../../packages/graph/src/timeline.ts";
import type { TimelineModel } from "../../../../packages/graph/src/timeline.ts";
import type { EntityKind } from "../../../../packages/types/src/api.ts";
import { kindVisual } from "../../../../packages/ui/src/tokens.ts";
import { clear, fill, h, s } from "./dom.ts";
import { provenanceBadge } from "./marks.ts";
import type { FailureText } from "./panels.ts";
import { describeTime, TIME_EMPTY, TIME_FAILED_UNFILTERED, TIME_LOADING } from "./time-model.ts";
import type { EventsRead, TimeView } from "./time-model.ts";

export type StripState =
  | { readonly kind: "loading" }
  | { readonly kind: "failed"; readonly failure: FailureText }
  | { readonly kind: "empty" }
  | { readonly kind: "ready"; readonly model: TimelineModel; readonly read: EventsRead };

export interface TimeStripHandlers {
  /** The chosen year, or null for every year. `final` is false while a drag or key repeat is still going. */
  readonly onYear: (year: number | null, final: boolean) => void;
  readonly onEntity: (id: string) => void;
  readonly onRetry: () => void;
}

/** Width of the slider's thumb in the stylesheet; the marks are inset by half of it so both share one scale. */
export const THUMB_PX = 20;
const ROW_PITCH = 10;
const AXIS_HEIGHT = 18;
const TOP_PAD = 6;
const MIN_MARK_ROWS = 3;
const PLAY_STEP_MS = 650;

export class TimeStrip {
  private readonly host: HTMLElement;
  private readonly atlas: Atlas;
  private readonly handlers: TimeStripHandlers;

  private readonly toggle: HTMLButtonElement;
  private readonly playButton: HTMLButtonElement;
  private readonly badges: HTMLElement;
  private readonly status: HTMLElement;
  private readonly reset: HTMLButtonElement;
  private readonly body: HTMLElement;
  private readonly chart: HTMLElement;
  private readonly slider: HTMLInputElement;
  private readonly yearLine: HTMLElement;
  private readonly warning: HTMLElement;
  private readonly notes: HTMLElement;
  private readonly noteBody: HTMLElement;
  private readonly resizeObserver: ResizeObserver;

  private state: StripState = { kind: "loading" };
  private year: number | null = null;
  private width = 0;
  private svg: SVGSVGElement | null = null;
  private marks: Array<{ readonly year: number; readonly element: SVGElement }> = [];
  private cursor: SVGElement | null = null;
  private expanded: boolean;
  private playTimer: number | null = null;

  constructor(host: HTMLElement, atlas: Atlas, handlers: TimeStripHandlers, options: { readonly expanded: boolean }) {
    this.host = host;
    this.atlas = atlas;
    this.handlers = handlers;
    this.expanded = options.expanded;

    this.toggle = h("button", { type: "button", class: "time-toggle", "aria-controls": "time-body", "aria-expanded": "true" }, "Timeline");
    this.toggle.addEventListener("click", () => this.setExpanded(!this.expanded));
    // Label switches between "Play" and "Pause"; intentionally no aria-pressed (WCAG / motion guidance).
    this.playButton = h("button", { type: "button", class: "text-button time-play", hidden: true }, "Play");
    this.playButton.addEventListener("click", () => this.togglePlay());
    this.badges = h("span", { class: "time-badges" });
    this.status = h("p", { class: "time-status" }, TIME_LOADING);
    this.reset = h("button", { type: "button", class: "text-button time-reset", hidden: true }, "Show every year");
    this.reset.addEventListener("click", () => {
      this.stopPlay();
      this.choose(null, true);
    });

    this.slider = h("input", { type: "range", class: "time-slider", step: "1", min: "0", max: "0", value: "0", disabled: true, "aria-label": "Show the map as of this year" });
    this.slider.addEventListener("input", () => {
      this.stopPlay();
      this.onSlider(false);
    });
    this.slider.addEventListener("change", () => {
      this.stopPlay();
      this.onSlider(true);
    });
    this.chart = h("div", { class: "time-chart" }, this.slider);
    this.yearLine = h("p", { class: "time-year-line" });
    this.warning = h("p", { class: "warn time-warning", hidden: true });
    this.noteBody = h("div", { class: "time-note-body" });
    this.notes = h("details", { class: "time-notes" }, h("summary", {}, "About these dates"), this.noteBody);
    this.body = h("div", { class: "time-body", id: "time-body" }, this.chart, this.warning, h("div", { class: "time-foot" }, this.yearLine, this.notes));

    fill(host, h("div", { class: "time-head" }, this.toggle, this.playButton, this.badges, this.status), this.body);

    this.resizeObserver = new ResizeObserver(() => {
      const width = Math.round(this.chart.getBoundingClientRect().width);
      if (width === this.width) return;
      this.width = width;
      this.drawMarks();
    });
    this.resizeObserver.observe(this.chart);
    this.applyExpanded();
    this.render();
  }

  destroy(): void {
    this.stopPlay();
    this.resizeObserver.disconnect();
  }

  setState(state: StripState): void {
    this.stopPlay();
    this.state = state;
    if (state.kind === "ready") {
      const model = state.model;
      this.slider.min = String(model.firstYear);
      this.slider.max = String(model.lastYear);
      this.slider.disabled = model.firstYear === model.lastYear;
      this.year = this.year === null ? null : clampYear(model, this.year);
      if (this.year !== null && this.year >= model.lastYear) this.year = null;
    }
    this.width = Math.round(this.chart.getBoundingClientRect().width);
    this.drawMarks();
    this.render();
  }

  /** Show `year` (null for every year) without telling the handlers: the caller already knows. */
  setYear(year: number | null): void {
    this.stopPlay();
    this.year = year;
    this.render();
  }

  /** The year shown now, as a reader sees it. */
  get currentYear(): number | null {
    return this.year;
  }

  stopPlay(): void {
    if (this.playTimer !== null) {
      window.clearInterval(this.playTimer);
      this.playTimer = null;
    }
    this.playButton.textContent = "Play";
  }

  private togglePlay(): void {
    if (this.playTimer !== null) {
      this.stopPlay();
      this.handlers.onYear(this.year, true);
      return;
    }
    if (this.state.kind !== "ready") return;
    const { model } = this.state;
    if (model.firstYear >= model.lastYear) return;
    if (!this.expanded) this.setExpanded(true);
    const start = this.year === null || this.year >= model.lastYear ? model.firstYear : this.year;
    this.playButton.textContent = "Pause";
    this.choose(start, false);
    this.playTimer = window.setInterval(() => this.tick(), PLAY_STEP_MS);
  }

  private tick(): void {
    if (this.state.kind !== "ready") {
      this.stopPlay();
      return;
    }
    const { model } = this.state;
    const next = (this.year ?? model.firstYear) + 1;
    if (next >= model.lastYear) {
      this.stopPlay();
      this.choose(null, true);
      return;
    }
    this.choose(next, false);
  }

  private setExpanded(expanded: boolean): void {
    if (!expanded) this.stopPlay();
    this.expanded = expanded;
    this.applyExpanded(); // the resize observer draws the marks once the chart has a width again
  }

  private applyExpanded(): void {
    this.toggle.setAttribute("aria-expanded", this.expanded ? "true" : "false");
    this.body.hidden = !this.expanded;
    this.host.classList.toggle("is-collapsed", !this.expanded);
  }

  private onSlider(final: boolean): void {
    if (this.state.kind !== "ready") return;
    const value = Number(this.slider.value);
    this.choose(value >= this.state.model.lastYear ? null : value, final);
  }

  private choose(year: number | null, final: boolean): void {
    this.year = year;
    this.render();
    this.handlers.onYear(year, final);
  }

  // ---- drawing ----------------------------------------------------------------------------------

  private view(): TimeView | null {
    return this.state.kind === "ready" ? describeTime(this.state.model, this.year, this.state.read) : null;
  }

  private render(): void {
    const state = this.state;
    this.host.dataset["state"] = state.kind;
    this.chart.hidden = state.kind !== "ready";
    this.yearLine.hidden = state.kind !== "ready";
    this.playButton.hidden = state.kind !== "ready" || state.model.firstYear >= state.model.lastYear;
    clear(this.noteBody);
    clear(this.badges);
    this.warning.hidden = true;
    this.notes.hidden = state.kind !== "ready";

    if (state.kind === "loading") {
      this.status.textContent = TIME_LOADING;
      this.reset.hidden = true;
      return;
    }
    if (state.kind === "empty") {
      this.status.textContent = TIME_EMPTY;
      this.reset.hidden = true;
      return;
    }
    if (state.kind === "failed") {
      const retry = h("button", { type: "button", class: "text-button" }, "Try again");
      retry.addEventListener("click", () => this.handlers.onRetry());
      clear(this.status);
      fill(this.status, h("strong", { class: "time-failure" }, state.failure.title), ` ${state.failure.body}`, state.failure.wait ? ` ${state.failure.wait}` : null, ` ${TIME_FAILED_UNFILTERED} `, state.failure.retryable ? retry : null);
      this.reset.hidden = this.year === null;
      if (this.year !== null) this.status.append(" ", this.reset);
      return;
    }

    const view = this.view();
    if (!view) return;
    this.slider.value = String(this.year ?? state.model.lastYear);
    this.slider.setAttribute("aria-valuetext", view.sliderText);
    this.status.textContent = view.status;
    this.reset.hidden = this.year === null;
    if (this.year !== null) this.status.append(" ", this.reset);

    const provenances = new Set([...state.model.dated.values()].map((entry) => entry.provenance));
    for (const provenance of [...provenances].sort()) this.badges.append(provenanceBadge(provenance));

    clear(this.yearLine);
    if (view.yearLine.empty !== null) {
      this.yearLine.append(view.yearLine.heading ? `${view.yearLine.heading} ` : "", view.yearLine.empty);
    } else {
      this.yearLine.append(`${view.yearLine.heading} `);
      const last = view.yearLine.entries.length - 1;
      view.yearLine.entries.forEach((entry, index) => {
        if (index > 0) this.yearLine.append(" ");
        const button = h("button", { type: "button", class: "text-button time-entity" }, entry.title);
        button.addEventListener("click", () => this.handlers.onEntity(entry.entityId));
        // The comma is held to the name it follows, so a line never starts with one.
        this.yearLine.append(h("span", { class: "time-entry" }, button, index < last ? "," : null));
      });
      if (view.yearLine.more > 0) this.yearLine.append(` and ${view.yearLine.more} more.`);
    }
    for (const note of view.notes) this.noteBody.append(h("p", { class: "note" }, note));
    this.warning.hidden = view.warning === null;
    this.warning.textContent = view.warning ?? "";
    this.dimLater();
  }

  private drawMarks(): void {
    this.svg?.remove();
    this.svg = null;
    this.marks = [];
    this.cursor = null;
    const state = this.state;
    if (state.kind !== "ready" || this.width <= THUMB_PX * 2) return;
    const { model } = state;

    const rows = Math.max(MIN_MARK_ROWS, model.tallest);
    const markArea = TOP_PAD + rows * ROW_PITCH;
    const height = markArea + AXIS_HEIGHT;
    const left = THUMB_PX / 2;
    const right = this.width - THUMB_PX / 2;
    const perYear = (right - left) / Math.max(1, model.lastYear - model.firstYear);
    const radius = perYear < 8 ? 2.6 : 3.4;
    const x = (year: number): number => yearToX(year, model.firstYear, model.lastYear, left, right);

    const svg = s("svg", { class: "time-marks", width: this.width, height, viewBox: `0 0 ${this.width} ${height}`, "aria-hidden": "true", focusable: "false" });
    svg.append(s("path", { class: "time-axis", d: `M${left} ${markArea}H${right}` }));
    for (const tick of axisTicks(model.firstYear, model.lastYear, this.width < 520 ? 4 : 8)) {
      svg.append(
        s("path", { class: "time-axis", d: `M${x(tick)} ${markArea}v4` }),
        s("text", { class: "time-tick", x: x(tick), y: markArea + 14, "text-anchor": "middle" }, String(tick)),
      );
    }
    for (const bucket of model.buckets) {
      bucket.entries.forEach((entry, index) => {
        const entity = this.atlas.byId.get(entry.entityId);
        const visual = kindVisual((entity?.kind ?? "technology") as EntityKind);
        const mark = s("path", { class: "time-mark", d: glyphPath(visual.shape, radius), transform: `translate(${x(bucket.year)} ${markArea - 6 - index * ROW_PITCH})`, fill: visual.color });
        svg.append(mark);
        this.marks.push({ year: bucket.year, element: mark });
      });
    }
    // The chosen year: a line down from the axis marks and a handle on the axis. The native slider above it is
    // invisible and takes the pointer and keyboard, so the handle only shows where it is.
    const cursor = s(
      "g",
      { class: "time-cursor" },
      s("path", { class: "time-line", d: `M0 0V${markArea}` }),
      s("rect", { class: "time-handle", x: -THUMB_PX / 2 + 3, y: markArea - 11, width: THUMB_PX - 6, height: 22, rx: (THUMB_PX - 6) / 2 }),
    );
    svg.append(cursor);
    this.cursor = cursor;
    this.svg = svg;
    this.chart.insertBefore(svg, this.slider);
    this.dimLater();
  }

  /** Marks of entities dated after the chosen year fade, and the cursor sits on the chosen year. */
  private dimLater(): void {
    const state = this.state;
    if (state.kind !== "ready") return;
    for (const mark of this.marks) mark.element.classList.toggle("is-later", this.year !== null && mark.year > this.year);
    if (this.cursor) {
      const { model } = state;
      const left = THUMB_PX / 2;
      const right = this.width - THUMB_PX / 2;
      const at = yearToX(this.year ?? model.lastYear, model.firstYear, model.lastYear, left, right);
      this.cursor.setAttribute("transform", `translate(${at} 0)`);
      this.cursor.classList.toggle("is-off", this.year === null); // the line only: the handle always shows where the slider is
    }
  }
}
