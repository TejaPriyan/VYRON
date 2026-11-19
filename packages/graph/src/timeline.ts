/**
 * The map's time axis: in which year each entity has its first dated event, and which entities have one by a
 * given year. Pure functions over the API's timeline events, with no DOM in them.
 *
 * What this does and does not say. An entity's year is the year of its earliest event on the timeline, so
 * "as of 1995" means "has an event dated 1995 or earlier". That is the first appearance only when the
 * earliest event is one (the development seed's events all are; real data may not be). An entity with no
 * event has no date at all, and nothing here guesses one: it is reported as undated and the caller decides
 * how to show it. Predictions are never placed on the axis, because they have not happened.
 */
import type { DatePrecision, Provenance, TimelineEvent } from "../../types/src/api.ts";
import { MAX_YEAR, MIN_YEAR } from "./map-state.ts";

/** The event an entity is dated by, and the year taken from it. */
export interface DatedEntity {
  readonly entityId: string;
  readonly year: number;
  readonly eventId: string;
  readonly title: string;
  readonly occurredOn: string;
  readonly precision: DatePrecision;
  readonly provenance: Provenance;
}

export interface YearBucket {
  readonly year: number;
  /** Sorted by title, then entity id. */
  readonly entries: ReadonlyArray<DatedEntity>;
}

export interface TimelineModel {
  readonly firstYear: number;
  readonly lastYear: number;
  readonly dated: ReadonlyMap<string, DatedEntity>;
  /** On the map but with no usable event; sorted. */
  readonly undatedIds: ReadonlyArray<string>;
  /** Ascending by year; only years that have an entity. */
  readonly buckets: ReadonlyArray<YearBucket>;
  /** The most entities dated to any one year. */
  readonly tallest: number;
  /** Events not used: predictions, events for entities that are not on the map, and dates that cannot be read. */
  readonly skipped: number;
}

/** The year of an ISO date (`YYYY-MM-DD`), or null for anything that is not a real calendar date in range. */
export function yearOf(isoDate: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < MIN_YEAR || year > MAX_YEAR) return null;
  const check = new Date(Date.UTC(year, month - 1, day));
  const real = check.getUTCFullYear() === year && check.getUTCMonth() === month - 1 && check.getUTCDate() === day;
  return real ? year : null;
}

const compareText = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

/**
 * Date every entity in `entityIds` by its earliest event. Returns null when none of them can be dated, so
 * the caller shows no time axis rather than an empty one.
 */
export function buildTimeline(events: ReadonlyArray<TimelineEvent>, entityIds: Iterable<string>): TimelineModel | null {
  const onMap = new Set(entityIds);
  const earliest = new Map<string, DatedEntity>();
  let skipped = 0;
  for (const event of events) {
    const year = yearOf(event.occurred_on);
    if (event.is_prediction || year === null || !onMap.has(event.entity_id)) {
      skipped += 1;
      continue;
    }
    const entry: DatedEntity = {
      entityId: event.entity_id,
      year,
      eventId: event.id,
      title: event.title,
      occurredOn: event.occurred_on,
      precision: event.date_precision,
      provenance: event.provenance,
    };
    const current = earliest.get(event.entity_id);
    const earlier = current === undefined || compareText(entry.occurredOn, current.occurredOn) < 0;
    const tie = current !== undefined && entry.occurredOn === current.occurredOn && compareText(entry.eventId, current.eventId) < 0;
    if (earlier || tie) earliest.set(event.entity_id, entry);
  }
  if (earliest.size === 0) return null;

  const grouped = new Map<number, DatedEntity[]>();
  for (const entry of earliest.values()) {
    const list = grouped.get(entry.year);
    if (list) list.push(entry);
    else grouped.set(entry.year, [entry]);
  }
  const buckets = [...grouped.entries()]
    .sort(([a], [b]) => a - b)
    .map(([year, entries]) => ({ year, entries: entries.sort((a, b) => compareText(a.title, b.title) || compareText(a.entityId, b.entityId)) }));
  const first = buckets[0];
  const last = buckets[buckets.length - 1];
  if (!first || !last) return null;
  return {
    firstYear: first.year,
    lastYear: last.year,
    dated: earliest,
    undatedIds: [...onMap].filter((id) => !earliest.has(id)).sort(compareText),
    buckets,
    tallest: Math.max(...buckets.map((bucket) => bucket.entries.length)),
    skipped,
  };
}

/** `year` moved inside the span the model covers, as a whole number. */
export function clampYear(model: TimelineModel, year: number): number {
  const whole = Number.isFinite(year) ? Math.round(year) : model.lastYear;
  return Math.min(model.lastYear, Math.max(model.firstYear, whole));
}

/** The dated entities whose first event comes after `year`: the ones the map leaves out as of that year. */
export function laterThan(model: TimelineModel, year: number): Set<string> {
  const later = new Set<string>();
  for (const [id, entry] of model.dated) if (entry.year > year) later.add(id);
  return later;
}

export interface AsOfCounts {
  /** Dated, with an event in or before the year. */
  readonly present: number;
  /** Dated, with the first event after the year. */
  readonly later: number;
  readonly undated: number;
}

export function countAsOf(model: TimelineModel, year: number): AsOfCounts {
  let present = 0;
  for (const entry of model.dated.values()) if (entry.year <= year) present += 1;
  return { present, later: model.dated.size - present, undated: model.undatedIds.length };
}

/** The entities whose first event falls in exactly `year`. */
export function datedIn(model: TimelineModel, year: number): ReadonlyArray<DatedEntity> {
  return model.buckets.find((bucket) => bucket.year === year)?.entries ?? [];
}

const TICK_STEPS: ReadonlyArray<number> = [1, 2, 5, 10, 20, 25, 50, 100];

/** Round years to label an axis from `first` to `last`: the finest round step that gives at most `maxTicks`. */
export function axisTicks(first: number, last: number, maxTicks: number): number[] {
  if (!(last >= first) || !Number.isFinite(first) || !Number.isFinite(last)) return [];
  const limit = Math.max(1, Math.floor(maxTicks));
  const step = TICK_STEPS.find((candidate) => Math.floor(last / candidate) - Math.ceil(first / candidate) + 1 <= limit) ?? 100;
  const ticks: number[] = [];
  for (let year = Math.ceil(first / step) * step; year <= last; year += step) ticks.push(year);
  return ticks;
}

/** Where `year` falls between `left` and `right` pixels, the first year at `left` and the last at `right`. */
export function yearToX(year: number, firstYear: number, lastYear: number, left: number, right: number): number {
  if (lastYear <= firstYear) return (left + right) / 2;
  const t = (Math.min(lastYear, Math.max(firstYear, year)) - firstYear) / (lastYear - firstYear);
  return left + t * (right - left);
}
