/**
 * A package's release dates as a strip: which x each release sits at, which row it is packed into, where the year
 * ticks go, and which release a key press moves to. Pure functions with no DOM.
 *
 * What the strip shows is what the registry recorded: one mark per release the API listed, placed at the date the API
 * gave. Nothing is interpolated and nothing is guessed. Two releases that are too close to be told apart at the
 * drawing's width are packed into different rows; when the rows run out the marks overlap, and that is counted and
 * reported (`crowded`) so the page can say so. The strip is a picture; the text list of every release is the record.
 */

export type ReleaseStatus = "normal" | "yanked" | "deprecated";

/** What the strip needs of one release: structurally the API's `ReleaseEntry`. */
export interface ReleaseInput {
  readonly version: string;
  readonly released_at: string;
  readonly pre_release: boolean;
  readonly status: ReleaseStatus;
}

export interface TimelineMetrics {
  /** Drawing width in pixels, including both side paddings. */
  readonly width: number;
  readonly padX: number;
  readonly padTop: number;
  /** Diameter of a mark. */
  readonly markSize: number;
  /** The least free space between two marks in one row. */
  readonly gap: number;
  /** The most rows marks are packed into. */
  readonly rows: number;
  readonly rowHeight: number;
  /** Height under the rows for the axis and its year labels. */
  readonly axisHeight: number;
  /** The least distance between two year labels. */
  readonly minTickGap: number;
}

export const DEFAULT_TIMELINE_METRICS: TimelineMetrics = {
  width: 640,
  padX: 22,
  padTop: 8,
  markSize: 9,
  gap: 2,
  rows: 6,
  rowHeight: 14,
  axisHeight: 26,
  minTickGap: 52,
};

/** The width the drawing is given for a history this many calendar years long: wide enough to read, never absurd. */
export const MIN_WIDTH = 480;
export const MAX_WIDTH = 1800;
export const PIXELS_PER_YEAR = 64;

export const widthForYears = (years: number): number => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(years * PIXELS_PER_YEAR)));

export interface Mark {
  /** The release's position in the list it was drawn from (oldest first). */
  readonly index: number;
  readonly version: string;
  readonly x: number;
  readonly y: number;
  readonly row: number;
  readonly shape: "circle" | "diamond";
  /** A pre-release by its version number is drawn hollow. */
  readonly hollow: boolean;
  /** No free space was left in any row, so this mark overlaps another. */
  readonly overlaps: boolean;
}

export interface Tick {
  readonly year: number;
  readonly x: number;
}

export interface TimelineLayout {
  readonly width: number;
  readonly height: number;
  readonly marks: ReadonlyArray<Mark>;
  readonly ticks: ReadonlyArray<Tick>;
  /** The y of the axis line. */
  readonly axisY: number;
  readonly firstYear: number;
  readonly lastYear: number;
  /** How many rows are in use. */
  readonly rowsUsed: number;
  /** How many marks overlap another because every row was full. */
  readonly crowded: number;
  /** Releases whose date could not be read, so they have no mark. */
  readonly unplaced: number;
}

/** The moment of an ISO timestamp in milliseconds, or null when it is not a date. */
export function momentOf(timestamp: string): number | null {
  const value = Date.parse(timestamp);
  return Number.isFinite(value) ? value : null;
}

const STEPS = [1, 2, 5, 10, 20, 50, 100] as const;

/** The smallest whole number of years between labels that keeps two labels at least `minGap` pixels apart. */
export function yearStep(spanYears: number, usableWidth: number, minGap: number): number {
  const span = Math.max(1, spanYears);
  for (const step of STEPS) if ((usableWidth * step) / span >= minGap) return step;
  return STEPS[STEPS.length - 1] as number;
}

export function shapeOf(status: ReleaseStatus): "circle" | "diamond" {
  return status === "normal" ? "circle" : "diamond";
}

/**
 * Place releases (oldest first, as the API sends them) on a time axis from 1 January of the first year to 1 January
 * after the last. Returns null for an empty list, so the caller shows no strip rather than an empty one.
 */
export function layoutReleases(releases: ReadonlyArray<ReleaseInput>, metrics: TimelineMetrics = DEFAULT_TIMELINE_METRICS): TimelineLayout | null {
  const dated: Array<{ readonly index: number; readonly at: number; readonly release: ReleaseInput }> = [];
  let unplaced = 0;
  releases.forEach((release, index) => {
    const at = momentOf(release.released_at);
    if (at === null) unplaced += 1;
    else dated.push({ index, at, release });
  });
  if (dated.length === 0) return null;

  let earliest = dated[0]?.at as number;
  let latest = earliest;
  for (const entry of dated) {
    if (entry.at < earliest) earliest = entry.at;
    if (entry.at > latest) latest = entry.at;
  }
  const firstYear = new Date(earliest).getUTCFullYear();
  const lastYear = new Date(latest).getUTCFullYear();
  const start = Date.UTC(firstYear, 0, 1);
  const end = Date.UTC(lastYear + 1, 0, 1);
  const usable = Math.max(1, metrics.width - 2 * metrics.padX);
  const xOf = (at: number): number => metrics.padX + ((at - start) / (end - start)) * usable;

  // Pack in time order: each mark goes into the first row whose last mark leaves room for it.
  const ordered = [...dated].sort((a, b) => a.at - b.at || a.index - b.index);
  const rightEdge: number[] = [];
  const half = metrics.markSize / 2;
  const placed: Array<{ entry: (typeof dated)[number]; x: number; row: number; overlaps: boolean }> = [];
  for (const entry of ordered) {
    const x = xOf(entry.at);
    let row = -1;
    for (let candidate = 0; candidate < rightEdge.length; candidate += 1) {
      if (x - half - metrics.gap >= (rightEdge[candidate] as number)) {
        row = candidate;
        break;
      }
    }
    let overlaps = false;
    if (row === -1 && rightEdge.length < metrics.rows) {
      row = rightEdge.length;
      rightEdge.push(-Infinity);
    }
    if (row === -1) {
      // Every row is in use and none has room: use the one whose last mark ends furthest left.
      overlaps = true;
      row = rightEdge.indexOf(Math.min(...rightEdge));
    }
    rightEdge[row] = x + half;
    placed.push({ entry, x, row, overlaps });
  }

  const rowsUsed = Math.max(1, rightEdge.length);
  const axisY = metrics.padTop + rowsUsed * metrics.rowHeight;
  const marks: Mark[] = placed
    .map(({ entry, x, row, overlaps }) => ({
      index: entry.index,
      version: entry.release.version,
      x,
      // Row 0 sits on the axis; further rows stack above it.
      y: axisY - (row + 0.5) * metrics.rowHeight,
      row,
      shape: shapeOf(entry.release.status),
      hollow: entry.release.pre_release,
      overlaps,
    }))
    .sort((a, b) => a.index - b.index);

  const step = yearStep(lastYear + 1 - firstYear, usable, metrics.minTickGap);
  let ticks: Tick[] = [];
  for (let year = firstYear; year <= lastYear + 1; year += 1) {
    if (year % step === 0) ticks.push({ year, x: xOf(Date.UTC(year, 0, 1)) });
  }
  // A short history may have no year divisible by the step: label where it starts, so the axis is never bare.
  if (ticks.length === 0) ticks = [{ year: firstYear, x: xOf(start) }];

  return {
    width: metrics.width,
    height: axisY + metrics.axisHeight,
    marks,
    ticks: dropCrowdedTicks(ticks, metrics.minTickGap),
    axisY,
    firstYear,
    lastYear,
    rowsUsed,
    crowded: marks.filter((mark) => mark.overlaps).length,
    unplaced,
  };
}

/** Keep each tick only if it is at least `minGap` right of the one kept before it, so labels never run together. */
export function dropCrowdedTicks(ticks: ReadonlyArray<Tick>, minGap: number): ReadonlyArray<Tick> {
  const kept: Tick[] = [];
  for (const tick of ticks) {
    const last = kept[kept.length - 1];
    if (last === undefined || tick.x - last.x >= minGap) kept.push(tick);
  }
  return kept;
}

export type ReleaseKey = "ArrowLeft" | "ArrowRight" | "ArrowUp" | "ArrowDown" | "Home" | "End" | "PageUp" | "PageDown";

/** How many releases PageUp and PageDown move. */
export const PAGE_STEP = 10;

/**
 * The release a key moves to, from `current` among `count` releases (oldest first), or null when the key does not
 * apply or nothing would change. Left and Up go to an earlier release, Right and Down to a later one.
 */
export function stepRelease(count: number, current: number, key: string): number | null {
  if (count <= 0 || current < 0 || current >= count) return null;
  let next: number;
  switch (key) {
    case "ArrowLeft":
    case "ArrowUp":
      next = current - 1;
      break;
    case "ArrowRight":
    case "ArrowDown":
      next = current + 1;
      break;
    case "PageUp":
      next = current - PAGE_STEP;
      break;
    case "PageDown":
      next = current + PAGE_STEP;
      break;
    case "Home":
      next = 0;
      break;
    case "End":
      next = count - 1;
      break;
    default:
      return null;
  }
  next = Math.min(count - 1, Math.max(0, next));
  return next === current ? null : next;
}
