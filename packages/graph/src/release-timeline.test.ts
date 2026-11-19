import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  DEFAULT_TIMELINE_METRICS,
  dropCrowdedTicks,
  layoutReleases,
  MAX_WIDTH,
  MIN_WIDTH,
  momentOf,
  PAGE_STEP,
  shapeOf,
  stepRelease,
  widthForYears,
  yearStep,
  type ReleaseInput,
  type ReleaseStatus,
  type TimelineMetrics,
} from "./release-timeline.ts";

// Made-up versions and dates, like the stand-in registry's: nothing here describes a real package.
const release = (version: string, released_at: string, status: ReleaseStatus = "normal", pre_release = false): ReleaseInput => ({
  version,
  released_at,
  pre_release,
  status,
});

const day = (year: number, month = 1, date = 1): string => `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(date).padStart(2, "0")}T10:00:00Z`;

const wide = (extra: Partial<TimelineMetrics> = {}): TimelineMetrics => ({ ...DEFAULT_TIMELINE_METRICS, ...extra });

function must<T>(value: T | null | undefined): T {
  assert.ok(value !== null && value !== undefined);
  return value;
}

describe("reading a date", () => {
  it("accepts an ISO timestamp with a zone and refuses anything else", () => {
    assert.equal(momentOf("2024-02-06T10:15:30.000000Z"), Date.UTC(2024, 1, 6, 10, 15, 30));
    assert.equal(momentOf("2024-02-06T12:15:30+02:00"), Date.UTC(2024, 1, 6, 10, 15, 30));
    for (const bad of ["", "yesterday", "2024-13-45T00:00:00Z", "nope"]) assert.equal(momentOf(bad), null, bad);
  });
});

describe("how far apart the year labels are", () => {
  it("takes the smallest step that keeps labels apart", () => {
    assert.equal(yearStep(3, 596, 52), 1);
    assert.equal(yearStep(12, 596, 52), 2);
    assert.equal(yearStep(35, 596, 52), 5);
    assert.equal(yearStep(1000, 596, 52), 100);
  });

  it("never divides by a span of zero or less", () => {
    assert.equal(yearStep(0, 596, 52), 1);
    assert.equal(yearStep(-4, 596, 52), 1);
  });

  it("drops a label that would run into the one before it", () => {
    const ticks = [0, 20, 60, 70, 130].map((x, index) => ({ year: 2000 + index, x }));
    assert.deepEqual(
      dropCrowdedTicks(ticks, 50).map((tick) => tick.x),
      [0, 60, 130],
    );
    assert.deepEqual(dropCrowdedTicks([], 50), []);
  });
});

describe("the shape of a mark", () => {
  it("is a circle for an ordinary release and a diamond for a yanked or deprecated one", () => {
    assert.equal(shapeOf("normal"), "circle");
    assert.equal(shapeOf("yanked"), "diamond");
    assert.equal(shapeOf("deprecated"), "diamond");
  });
});

describe("how wide the drawing is", () => {
  it("grows with the history, within bounds", () => {
    assert.equal(widthForYears(1), MIN_WIDTH);
    assert.equal(widthForYears(12), 768);
    assert.equal(widthForYears(500), MAX_WIDTH);
  });
});

describe("placing releases", () => {
  it("draws nothing for no releases, and nothing when no date can be read", () => {
    assert.equal(layoutReleases([]), null);
    assert.equal(layoutReleases([release("1.0.0", "someday")]), null);
  });

  it("counts a release whose date cannot be read and gives it no mark", () => {
    const layout = must(layoutReleases([release("1.0.0", day(2020)), release("1.1.0", "soon"), release("1.2.0", day(2021))]));
    assert.equal(layout.unplaced, 1);
    assert.deepEqual(
      layout.marks.map((mark) => mark.version),
      ["1.0.0", "1.2.0"],
    );
    assert.deepEqual(
      layout.marks.map((mark) => mark.index),
      [0, 2],
    );
  });

  it("runs from 1 January of the first year to 1 January after the last", () => {
    const metrics = wide();
    const layout = must(layoutReleases([release("1.0.0", "2014-01-01T00:00:00Z"), release("2.0.0", "2025-12-31T23:59:59Z")], metrics));
    assert.equal(layout.firstYear, 2014);
    assert.equal(layout.lastYear, 2025);
    assert.equal(must(layout.marks[0]).x, metrics.padX);
    const last = must(layout.marks[1]).x;
    assert.ok(last < metrics.width - metrics.padX && last > metrics.width - metrics.padX - 1, `last mark at ${last}`);
  });

  it("puts later releases further right", () => {
    const layout = must(layoutReleases([release("1", day(2015, 3)), release("2", day(2015, 9)), release("3", day(2017, 1))]));
    const xs = layout.marks.map((mark) => mark.x);
    assert.deepEqual(xs, [...xs].sort((a, b) => a - b));
    assert.ok(xs[0] !== xs[1] && xs[1] !== xs[2]);
  });

  it("places one release inside its year, with the year labelled", () => {
    const layout = must(layoutReleases([release("1.0.0", day(2020, 7, 1))]));
    assert.equal(layout.marks.length, 1);
    assert.equal(layout.rowsUsed, 1);
    assert.equal(layout.firstYear, layout.lastYear);
    assert.ok(layout.ticks.length >= 1);
    const x = must(layout.marks[0]).x;
    assert.ok(x > DEFAULT_TIMELINE_METRICS.padX && x < DEFAULT_TIMELINE_METRICS.width - DEFAULT_TIMELINE_METRICS.padX);
  });

  it("keeps each mark's place in the list it was given, in any order", () => {
    const layout = must(layoutReleases([release("b", day(2021)), release("a", day(2019))]));
    assert.deepEqual(
      layout.marks.map((mark) => [mark.index, mark.version]),
      [
        [0, "b"],
        [1, "a"],
      ],
    );
    assert.ok(must(layout.marks[1]).x < must(layout.marks[0]).x);
  });

  it("marks a pre-release by its number as hollow and a yanked or deprecated one as a diamond", () => {
    const layout = must(
      layoutReleases([
        release("1.0.0", day(2020)),
        release("1.1.0rc1", day(2021), "normal", true),
        release("1.1.0", day(2022), "yanked"),
        release("2.0.0b1", day(2023), "deprecated", true),
      ]),
    );
    assert.deepEqual(
      layout.marks.map((mark) => [mark.shape, mark.hollow]),
      [
        ["circle", false],
        ["circle", true],
        ["diamond", false],
        ["diamond", true],
      ],
    );
  });
});

describe("packing marks into rows", () => {
  it("puts the first row on the axis and stacks further rows above it", () => {
    const metrics = wide();
    const layout = must(layoutReleases([release("a", day(2020, 5, 1)), release("b", day(2020, 5, 1))], metrics));
    const [first, second] = layout.marks;
    assert.equal(must(first).row, 0);
    assert.equal(must(second).row, 1);
    assert.equal(must(first).y, layout.axisY - metrics.rowHeight / 2);
    assert.ok(must(second).y < must(first).y);
    assert.equal(layout.height, layout.axisY + metrics.axisHeight);
    assert.equal(layout.rowsUsed, 2);
  });

  it("uses one row when releases are far enough apart", () => {
    const layout = must(layoutReleases([release("a", day(2015)), release("b", day(2018)), release("c", day(2021))]));
    assert.equal(layout.rowsUsed, 1);
    assert.equal(layout.crowded, 0);
  });

  it("reports marks that overlap once every row is full, and says so only then", () => {
    const metrics = wide({ rows: 3 });
    const same = Array.from({ length: 5 }, (_unused, index) => release(`v${index}`, day(2020, 5, 1)));
    const layout = must(layoutReleases(same, metrics));
    assert.equal(layout.rowsUsed, 3);
    assert.equal(layout.crowded, 2);
    assert.deepEqual(
      layout.marks.map((mark) => mark.overlaps),
      [false, false, false, true, true],
    );
    const exactly = must(layoutReleases(same.slice(0, 3), metrics));
    assert.equal(exactly.crowded, 0);
  });

  it("never places two marks on top of each other unless every row is full", () => {
    // 312 releases about 19 days apart over 16 years, like the stand-in's wide package.
    const releases: ReleaseInput[] = [];
    let at = Date.UTC(2010, 0, 4);
    for (let index = 0; index < 312; index += 1) {
      releases.push(release(`1.${Math.floor(index / 40)}.${index % 40}`, new Date(at).toISOString()));
      at += 19 * 86_400_000;
    }
    for (const metrics of [wide(), wide({ width: 1100 }), wide({ rows: 2 })]) {
      const layout = must(layoutReleases(releases, metrics));
      assert.equal(layout.marks.length, 312);
      const byRow = new Map<number, typeof layout.marks[number][]>();
      for (const mark of layout.marks) byRow.set(mark.row, [...(byRow.get(mark.row) ?? []), mark]);
      for (const marks of byRow.values()) {
        const ordered = [...marks].sort((a, b) => a.x - b.x || a.index - b.index);
        ordered.forEach((mark, position) => {
          const before = ordered[position - 1];
          if (before && !mark.overlaps) assert.ok(mark.x - before.x >= metrics.markSize + metrics.gap - 1e-9, `row ${mark.row}: ${before.x} then ${mark.x}`);
        });
      }
      if (layout.crowded > 0) assert.equal(layout.rowsUsed, metrics.rows);
    }
  });

  it("is the same for the same input", () => {
    const releases = Array.from({ length: 40 }, (_unused, index) => release(`1.${index}`, day(2015 + (index % 7), 1 + (index % 12), 1 + (index % 27))));
    assert.deepEqual(layoutReleases(releases), layoutReleases(releases));
  });
});

describe("the year axis", () => {
  it("labels every second year for a twelve-year history at the default width", () => {
    const layout = must(layoutReleases([release("a", day(2014, 3, 12)), release("b", day(2025, 4, 17))]));
    assert.deepEqual(
      layout.ticks.map((tick) => tick.year),
      [2014, 2016, 2018, 2020, 2022, 2024, 2026],
    );
    layout.ticks.forEach((tick, position) => {
      const before = layout.ticks[position - 1];
      if (before) assert.ok(tick.x - before.x >= DEFAULT_TIMELINE_METRICS.minTickGap);
    });
  });

  it("labels the years divisible by the step for a long history", () => {
    const layout = must(layoutReleases([release("a", day(1993)), release("b", day(2025))]));
    assert.deepEqual(
      layout.ticks.map((tick) => tick.year),
      [1995, 2000, 2005, 2010, 2015, 2020, 2025],
    );
  });

  it("puts a tick's x where 1 January of that year is", () => {
    const metrics = wide();
    const layout = must(layoutReleases([release("a", day(2020)), release("b", day(2021, 12, 31))], metrics));
    assert.equal(must(layout.ticks[0]).year, 2020);
    assert.equal(must(layout.ticks[0]).x, metrics.padX);
    assert.ok(Math.abs(must(layout.ticks[layout.ticks.length - 1]).x - (metrics.width - metrics.padX)) < 1e-9);
  });

  it("still labels the start of a short history when no year divides the step", () => {
    const layout = must(layoutReleases([release("a", day(2016)), release("b", day(2018))], wide({ width: 100 })));
    assert.deepEqual(
      layout.ticks.map((tick) => tick.year),
      [2016],
    );
  });
});

describe("moving between releases with the keys", () => {
  it("moves one release, to the ends, and by pages, never past either end", () => {
    assert.equal(stepRelease(30, 10, "ArrowLeft"), 9);
    assert.equal(stepRelease(30, 10, "ArrowUp"), 9);
    assert.equal(stepRelease(30, 10, "ArrowRight"), 11);
    assert.equal(stepRelease(30, 10, "ArrowDown"), 11);
    assert.equal(stepRelease(30, 10, "Home"), 0);
    assert.equal(stepRelease(30, 10, "End"), 29);
    assert.equal(stepRelease(30, 20, "PageUp"), 20 - PAGE_STEP);
    assert.equal(stepRelease(30, 5, "PageUp"), 0);
    assert.equal(stepRelease(30, 25, "PageDown"), 29);
  });

  it("says nothing when nothing would change or the key is not one of these", () => {
    assert.equal(stepRelease(30, 0, "ArrowLeft"), null);
    assert.equal(stepRelease(30, 29, "ArrowRight"), null);
    assert.equal(stepRelease(30, 0, "Home"), null);
    assert.equal(stepRelease(30, 29, "End"), null);
    assert.equal(stepRelease(30, 3, "a"), null);
    assert.equal(stepRelease(30, 3, "Enter"), null);
  });

  it("refuses an empty list and a position that is not in it", () => {
    assert.equal(stepRelease(0, 0, "ArrowRight"), null);
    assert.equal(stepRelease(5, -1, "ArrowRight"), null);
    assert.equal(stepRelease(5, 5, "ArrowLeft"), null);
  });

  it("walks a list from one end to the other", () => {
    let at = 0;
    const seen = [at];
    for (let step = stepRelease(4, at, "ArrowRight"); step !== null; step = stepRelease(4, at, "ArrowRight")) {
      at = step;
      seen.push(at);
    }
    assert.deepEqual(seen, [0, 1, 2, 3]);
  });
});
