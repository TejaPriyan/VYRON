import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { TimelineEvent } from "../../types/src/api.ts";
import { axisTicks, buildTimeline, clampYear, countAsOf, datedIn, laterThan, yearOf, yearToX } from "./timeline.ts";

let counter = 0;
function event(entity: string, occurredOn: string, title: string, extra: Partial<TimelineEvent> = {}): TimelineEvent {
  counter += 1;
  return {
    id: `event-${String(counter).padStart(3, "0")}`,
    entity_id: entity,
    occurred_on: occurredOn,
    date_precision: "year",
    title,
    event_type: "release",
    provenance: "seed",
    is_prediction: false,
    description: null,
    ...extra,
  };
}

describe("yearOf", () => {
  it("reads the year of a real calendar date", () => {
    assert.equal(yearOf("1991-01-01"), 1991);
    assert.equal(yearOf("2024-02-29"), 2024);
  });

  it("refuses anything that is not a real date inside the supported range", () => {
    for (const bad of ["", "1991", "1991-1-1", "1991-13-01", "1991-02-30", "2023-02-29", "1899-12-31", "2201-01-01", "1991-01-01T00:00:00Z", "x991-01-01"]) {
      assert.equal(yearOf(bad), null, bad);
    }
  });
});

describe("buildTimeline", () => {
  const ids = ["a", "b", "c", "d"];

  it("dates each entity by its earliest event and lists the rest as undated", () => {
    const model = buildTimeline(
      [
        event("a", "1995-01-01", "A released"),
        event("a", "1991-01-01", "A announced"),
        event("b", "2005-01-01", "B released"),
        event("c", "1995-01-01", "C released"),
      ],
      ids,
    );
    assert.ok(model);
    assert.equal(model.dated.get("a")?.year, 1991);
    assert.equal(model.dated.get("a")?.title, "A announced");
    assert.deepEqual(model.undatedIds, ["d"]);
    assert.equal(model.firstYear, 1991);
    assert.equal(model.lastYear, 2005);
    assert.deepEqual(model.buckets.map((bucket) => [bucket.year, bucket.entries.map((e) => e.entityId)]), [[1991, ["a"]], [1995, ["c"]], [2005, ["b"]]]);
    assert.equal(model.tallest, 1);
  });

  it("breaks a same-day tie on the event id, whatever order the events arrive in", () => {
    const one = event("a", "2000-06-01", "Second", { id: "e-2" });
    const two = event("a", "2000-06-01", "First", { id: "e-1" });
    assert.equal(buildTimeline([one, two], ["a"])?.dated.get("a")?.eventId, "e-1");
    assert.equal(buildTimeline([two, one], ["a"])?.dated.get("a")?.eventId, "e-1");
  });

  it("sorts the entities of one year by title", () => {
    const model = buildTimeline([event("a", "2009-01-01", "Zed"), event("b", "2009-01-01", "Alpha"), event("c", "2009-01-01", "Mid")], ids);
    assert.deepEqual(model?.buckets[0]?.entries.map((e) => e.title), ["Alpha", "Mid", "Zed"]);
    assert.equal(model?.tallest, 3);
  });

  it("never places a prediction, an event for an entity that is not on the map, or an unreadable date", () => {
    const model = buildTimeline(
      [
        event("a", "2040-01-01", "A in the future", { provenance: "predicted", is_prediction: true }),
        event("zzz", "1990-01-01", "Not on the map"),
        event("b", "not-a-date", "Broken date"),
        event("c", "1999-01-01", "C released"),
      ],
      ids,
    );
    assert.ok(model);
    assert.deepEqual([...model.dated.keys()], ["c"]);
    assert.equal(model.skipped, 3);
    assert.deepEqual(model.undatedIds, ["a", "b", "d"]);
  });

  it("returns null when nothing can be dated, so no axis is drawn", () => {
    assert.equal(buildTimeline([], ids), null);
    assert.equal(buildTimeline([event("zzz", "1990-01-01", "Elsewhere")], ids), null);
  });

  it("keeps the precision and provenance of the event it used", () => {
    const model = buildTimeline([event("a", "1990-01-01", "A in the 1990s", { date_precision: "decade", provenance: "estimated" })], ids);
    assert.equal(model?.dated.get("a")?.precision, "decade");
    assert.equal(model?.dated.get("a")?.provenance, "estimated");
  });
});

describe("as of a year", () => {
  const model = buildTimeline(
    [event("a", "1991-01-01", "A"), event("b", "1995-01-01", "B"), event("c", "1995-01-01", "C"), event("d", "2010-01-01", "D")],
    ["a", "b", "c", "d", "e", "f"],
  );
  assert.ok(model);

  it("counts entities present, later and undated", () => {
    assert.deepEqual(countAsOf(model, 1990), { present: 0, later: 4, undated: 2 });
    assert.deepEqual(countAsOf(model, 1991), { present: 1, later: 3, undated: 2 });
    assert.deepEqual(countAsOf(model, 1995), { present: 3, later: 1, undated: 2 });
    assert.deepEqual(countAsOf(model, 2010), { present: 4, later: 0, undated: 2 });
  });

  it("names the entities left out, never an undated one", () => {
    assert.deepEqual([...laterThan(model, 1994)].sort(), ["b", "c", "d"]);
    assert.deepEqual([...laterThan(model, 2010)], []);
    assert.ok(!laterThan(model, 1900).has("e"));
  });

  it("lists what was dated in exactly one year", () => {
    assert.deepEqual(datedIn(model, 1995).map((e) => e.entityId), ["b", "c"]);
    assert.deepEqual(datedIn(model, 1996), []);
  });

  it("keeps a chosen year inside the span", () => {
    assert.equal(clampYear(model, 1850), 1991);
    assert.equal(clampYear(model, 2500), 2010);
    assert.equal(clampYear(model, 1999.6), 2000);
    assert.equal(clampYear(model, Number.NaN), 2010);
  });
});

describe("axisTicks", () => {
  it("uses round years and no more than asked for", () => {
    assert.deepEqual(axisTicks(1972, 2023, 6), [1980, 1990, 2000, 2010, 2020]);
    assert.deepEqual(axisTicks(1972, 2023, 3), [1980, 2000, 2020]);
    assert.deepEqual(axisTicks(1998, 2003, 8), [1998, 1999, 2000, 2001, 2002, 2003]);
    for (const limit of [1, 2, 3, 5, 8]) assert.ok(axisTicks(1972, 2023, limit).length <= limit, String(limit));
  });

  it("copes with one year, a reversed span and nonsense", () => {
    assert.deepEqual(axisTicks(2000, 2000, 4), [2000]);
    assert.deepEqual(axisTicks(2000, 1990, 4), []);
    assert.deepEqual(axisTicks(Number.NaN, 1990, 4), []);
  });
});

describe("yearToX", () => {
  it("puts the first year at the left, the last at the right and the middle between", () => {
    assert.equal(yearToX(1990, 1990, 2000, 10, 110), 10);
    assert.equal(yearToX(2000, 1990, 2000, 10, 110), 110);
    assert.equal(yearToX(1995, 1990, 2000, 10, 110), 60);
  });

  it("clamps a year outside the span and centres a single-year span", () => {
    assert.equal(yearToX(1900, 1990, 2000, 10, 110), 10);
    assert.equal(yearToX(2100, 1990, 2000, 10, 110), 110);
    assert.equal(yearToX(2000, 2000, 2000, 10, 110), 60);
  });
});
