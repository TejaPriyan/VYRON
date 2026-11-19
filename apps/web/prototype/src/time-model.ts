/**
 * What the timeline strip says: the words for each year the map can be shown as of. Pure functions with no DOM,
 * so every sentence is tested. The strip draws what this returns; it adds nothing of its own.
 */
import type { DatedEntity, TimelineModel } from "../../../../packages/graph/src/timeline.ts";
import { countAsOf, datedIn } from "../../../../packages/graph/src/timeline.ts";
import { plural } from "./dom.ts";

/** How many entities of one year are named under the strip before the rest are counted. */
export const MAX_NAMED_PER_YEAR = 8;

export const HOW_DATES_ARE_PLACED = "Each entity is placed in the year of its earliest event on the timeline.";
export const SLIDER_HINT = "Move the slider to see the map as it stood in an earlier year.";

/** What was read from the API, as far as the strip needs to say how much of it there is. */
export interface EventsRead {
  readonly loaded: number;
  readonly expected: number;
  readonly complete: boolean;
  readonly notice: string | null;
}

export interface YearLine {
  readonly heading: string;
  readonly entries: ReadonlyArray<DatedEntity>;
  /** Entities of that year not listed, because the list stops at `MAX_NAMED_PER_YEAR`. */
  readonly more: number;
  /** Set instead of `entries` when nothing is dated that year, or when no year is chosen. */
  readonly empty: string | null;
}

export interface TimeView {
  /** The year the map is shown as of, or null for every year. */
  readonly year: number | null;
  readonly status: string;
  /** For `aria-valuetext` on the slider. */
  readonly sliderText: string;
  readonly yearLine: YearLine;
  /** How the dates were placed, what the API said about them and what the map does with undated entities. */
  readonly notes: ReadonlyArray<string>;
  /** Set when part of the events could not be read; shown where it cannot be missed, not folded away with the notes. */
  readonly warning: string | null;
}

const EN_DASH = "–";

export function describeTime(model: TimelineModel, year: number | null, read: EventsRead): TimeView {
  const dated = model.dated.size;
  const undated = model.undatedIds.length;
  const total = dated + undated;
  const notes: string[] = [HOW_DATES_ARE_PLACED];
  if (undated > 0) notes.push(`${plural(undated, "entity has", "entities have")} no date on the timeline. ${undated === 1 ? "It stays" : "They stay"} on the map in every year, dimmed while a year is chosen.`);
  if (model.skipped > 0) notes.push(`${plural(model.skipped, "event was", "events were")} not placed: a forecast, a date that is not a year from 1900 to 2200, or an entity that is not on the map.`);
  if (read.notice) notes.push(read.notice);
  const warning = read.complete ? null : `Only ${read.loaded} of ${plural(read.expected, "event", "events")} could be read, so some entities may be missing a date.`;

  if (year === null) {
    return {
      year: null,
      status: `Every year, ${model.firstYear}${EN_DASH}${model.lastYear}. ${dated} of ${plural(total, "entity", "entities")} ${dated === 1 ? "has" : "have"} a date.`,
      sliderText: `Every year, up to ${model.lastYear}`,
      yearLine: { heading: "", entries: [], more: 0, empty: SLIDER_HINT },
      notes,
      warning,
    };
  }

  const counts = countAsOf(model, year);
  const inYear = datedIn(model, year);
  const present = counts.present === 0 ? `none of the ${plural(dated, "dated entity", "dated entities")} yet` : `${counts.present} of ${plural(dated, "dated entity", "dated entities")}`;
  const stay = undated > 0 ? ` ${undated} without a date ${undated === 1 ? "is" : "are"} shown dimmed.` : "";
  return {
    year,
    status: `Map as of ${year}: ${present}.${stay}`,
    sliderText: `${year}, ${counts.present} of ${dated} dated entities present`,
    yearLine:
      inYear.length === 0
        ? { heading: `Dated ${year}:`, entries: [], more: 0, empty: `Nothing on the timeline is dated ${year}.` }
        : { heading: `Dated ${year}:`, entries: inYear.slice(0, MAX_NAMED_PER_YEAR), more: Math.max(0, inYear.length - MAX_NAMED_PER_YEAR), empty: null },
    notes,
    warning,
  };
}

/** The strip's own words when it has no model to describe. */
export const TIME_LOADING = "Reading the dates…";
export const TIME_EMPTY = "No entity on the map has a date on the timeline, so there is no time axis to show.";
export const TIME_FAILED_UNFILTERED = "The map is not filtered by year until the dates load.";

/** One line for a year in an address that the timeline cannot show, naming what was done instead. */
export function describeYearClamp(requested: number, model: TimelineModel, used: number | null): string | null {
  if (used === requested) return null;
  if (used === null && requested <= model.lastYear) return null; // the last year is "every year" already
  if (used === null) return `The year ${requested} in this link is after the last dated event (${model.lastYear}), so the map shows every year.`;
  return `The year ${requested} in this link is before the first dated event (${model.firstYear}), so the map starts at ${used}.`;
}
