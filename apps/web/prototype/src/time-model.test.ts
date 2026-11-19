import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { buildTimeline } from "../../../../packages/graph/src/timeline.ts";
import type { TimelineEvent } from "../../../../packages/types/src/api.ts";
import { describeTime, describeYearClamp, HOW_DATES_ARE_PLACED, MAX_NAMED_PER_YEAR, SLIDER_HINT } from "./time-model.ts";

let counter = 0;
const event = (entity: string, year: number, title: string): TimelineEvent => {
  counter += 1;
  return { id: `ev-${String(counter).padStart(3, "0")}`, entity_id: entity, occurred_on: `${year}-01-01`, date_precision: "year", title, event_type: "release", provenance: "seed", is_prediction: false, description: null };
};
const complete = { loaded: 4, expected: 4, complete: true, notice: null };

const model = buildTimeline(
  [event("a", 1991, "A first released"), event("b", 1995, "B first released"), event("c", 1995, "C first released"), event("d", 2010, "D first released")],
  ["a", "b", "c", "d", "e", "f"],
);
assert.ok(model);

describe("describeTime with no year chosen", () => {
  const view = describeTime(model, null, complete);

  it("says every year is shown and how many entities have a date", () => {
    assert.equal(view.year, null);
    assert.equal(view.status, "Every year, 1991–2010. 4 of 6 entities have a date.");
    assert.equal(view.sliderText, "Every year, up to 2010");
  });

  it("tells the reader what the slider does instead of listing a year", () => {
    assert.equal(view.yearLine.empty, SLIDER_HINT);
    assert.deepEqual(view.yearLine.entries, []);
  });

  it("explains how a date is chosen and what undated entities do", () => {
    assert.equal(view.notes[0], HOW_DATES_ARE_PLACED);
    assert.ok(view.notes.includes("2 entities have no date on the timeline. They stay on the map in every year, dimmed while a year is chosen."));
  });
});

describe("describeTime with events that cannot be placed", () => {
  const forecast: TimelineEvent = { ...event("a", 2030, "A forecast"), is_prediction: true };
  const elsewhere = event("not-on-the-map", 1990, "Somewhere else");
  const odd: TimelineEvent = { ...event("b", 1990, "Odd date"), occurred_on: "soon" };

  it("says how many were left out and why", () => {
    const flawed = buildTimeline([event("a", 1991, "A first released"), forecast, elsewhere, odd], ["a", "b"]);
    assert.ok(flawed);
    assert.ok(describeTime(flawed, null, complete).notes.includes("3 events were not placed: a forecast, a date that is not a year from 1900 to 2200, or an entity that is not on the map."));
  });

  it("is silent when every event was placed", () => {
    assert.ok(!describeTime(model, null, complete).notes.some((note) => note.includes("not placed")));
  });

  it("uses the singular for one", () => {
    const flawed = buildTimeline([event("a", 1991, "A first released"), forecast], ["a"]);
    assert.ok(flawed);
    assert.ok(describeTime(flawed, null, complete).notes.includes("1 event was not placed: a forecast, a date that is not a year from 1900 to 2200, or an entity that is not on the map."));
  });
});

describe("describeTime as of a year", () => {
  it("counts what is present and says undated entities are shown dimmed", () => {
    const view = describeTime(model, 1995, complete);
    assert.equal(view.status, "Map as of 1995: 3 of 4 dated entities. 2 without a date are shown dimmed.");
    assert.equal(view.sliderText, "1995, 3 of 4 dated entities present");
  });

  it("lists the entities dated in exactly that year", () => {
    const line = describeTime(model, 1995, complete).yearLine;
    assert.equal(line.heading, "Dated 1995:");
    assert.deepEqual(line.entries.map((e) => e.entityId), ["b", "c"]);
    assert.equal(line.more, 0);
    assert.equal(line.empty, null);
  });

  it("says plainly when nothing is dated that year", () => {
    const line = describeTime(model, 1996, complete).yearLine;
    assert.deepEqual(line.entries, []);
    assert.equal(line.empty, "Nothing on the timeline is dated 1996.");
  });

  it("says none are present before the first date, and that is not an error", () => {
    assert.equal(describeTime(model, 1980, complete).status, "Map as of 1980: none of the 4 dated entities yet. 2 without a date are shown dimmed.");
  });

  it("leaves out the undated sentence when every entity has a date", () => {
    const all = buildTimeline([event("a", 2000, "A"), event("b", 2001, "B")], ["a", "b"]);
    assert.ok(all);
    const view = describeTime(all, 2000, complete);
    assert.equal(view.status, "Map as of 2000: 1 of 2 dated entities.");
    assert.ok(!view.notes.some((note) => note.includes("no date")));
  });

  it("names at most eight entities of one year and counts the rest", () => {
    const ids = Array.from({ length: 11 }, (_, i) => `x${i}`);
    const crowded = buildTimeline(ids.map((id) => event(id, 2009, `${id} released`)), ids);
    assert.ok(crowded);
    const line = describeTime(crowded, 2009, complete).yearLine;
    assert.equal(line.entries.length, MAX_NAMED_PER_YEAR);
    assert.equal(line.more, 3);
  });

  it("uses the singular for one entity", () => {
    const one = buildTimeline([event("a", 2000, "A")], ["a", "b"]);
    assert.ok(one);
    assert.equal(describeTime(one, null, complete).status, "Every year, 2000–2000. 1 of 2 entities has a date.");
    assert.ok(describeTime(one, null, complete).notes.includes("1 entity has no date on the timeline. It stays on the map in every year, dimmed while a year is chosen."));
    assert.equal(describeTime(one, 2000, complete).status, "Map as of 2000: 1 of 1 dated entity. 1 without a date is shown dimmed.");
  });
});

describe("what the API said about the events", () => {
  it("passes on the API's own note", () => {
    const view = describeTime(model, null, { ...complete, notice: "Development seed data. Not verified." });
    assert.ok(view.notes.includes("Development seed data. Not verified."));
  });

  it("warns, with the numbers, when only some of the events were read", () => {
    const view = describeTime(model, 1995, { loaded: 500, expected: 812, complete: false, notice: null });
    assert.equal(view.warning, "Only 500 of 812 events could be read, so some entities may be missing a date.");
    assert.ok(!view.notes.some((note) => note.startsWith("Only")), "a warning is not folded in with the notes");
    assert.equal(describeTime(model, null, { loaded: 500, expected: 812, complete: false, notice: null }).warning, view.warning);
  });

  it("adds no warning for a complete read", () => {
    assert.equal(describeTime(model, 1995, complete).warning, null);
    assert.equal(describeTime(model, null, complete).warning, null);
  });
});

describe("describeYearClamp", () => {
  it("says nothing when the year was used as given", () => {
    assert.equal(describeYearClamp(1995, model, 1995), null);
  });

  it("says nothing for the last year, which already means every year", () => {
    assert.equal(describeYearClamp(2010, model, null), null);
  });

  it("says what was done for a year before the first or after the last date", () => {
    assert.equal(describeYearClamp(1950, model, 1991), "The year 1950 in this link is before the first dated event (1991), so the map starts at 1991.");
    assert.equal(describeYearClamp(2100, model, null), "The year 2100 in this link is after the last dated event (2010), so the map shows every year.");
  });
});
