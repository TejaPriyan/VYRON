/** Small pure text helpers for the panels. */
import type { DatePrecision } from "../../../../packages/types/src/api.ts";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

/**
 * An event date at the precision it is known to: a year-precision event shows "1991", never an
 * invented "1 Jan 1991". Returns the input unchanged if it is not an ISO date.
 */
export function formatEventDate(isoDate: string, precision: DatePrecision): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!match) return isoDate;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (precision === "decade") return `${Math.floor(year / 10) * 10}s`;
  if (precision === "year") return String(year);
  const monthName = MONTHS[month - 1];
  if (!monthName) return isoDate;
  if (precision === "month") return `${monthName} ${year}`;
  return `${day} ${monthName} ${year}`;
}

/**
 * Splits "React is built on JavaScript." into the words before the other entity's name and the name,
 * so the name can be a link. Relationship sentences are built as "<this> <phrase> <other>.", so the
 * name is always the last thing before the full stop; anything else is returned as plain text.
 */
export function splitSentence(sentence: string, otherName: string): { before: string; name: string; after: string } {
  const tail = `${otherName}.`;
  if (sentence.endsWith(tail) && sentence.length > tail.length) {
    return { before: sentence.slice(0, sentence.length - tail.length), name: otherName, after: "." };
  }
  return { before: sentence, name: "", after: "" };
}

/** "Ranked 1 of 17" or "Ranked 4 of 17, tied": position by score with ties sharing the better rank. */
export function describeRank(value: number, allValues: ReadonlyArray<number>): string {
  const better = allValues.filter((v) => v > value).length;
  const tied = allValues.filter((v) => v === value).length > 1;
  return `Ranked ${better + 1} of ${allValues.length}${tied ? ", tied" : ""}`;
}
