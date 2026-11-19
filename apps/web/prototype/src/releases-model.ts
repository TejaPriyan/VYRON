/**
 * The release history's words and numbers with no DOM in them, so every sentence the page shows can be tested. Nothing
 * here fetches anything and nothing is invented: it turns what the API returned (`PackageReleases`, an error) into
 * sentences, rows and counts. What a date means, what was left out and why, and how old the answer is are always said.
 */
import type { PackageRegistry } from "../../../../packages/graph/src/dependency-graph.ts";
import type { PackageReleases, ReleaseEntry, ReleaseLeftOut } from "../../../../packages/types/src/api.ts";
import { plural } from "./dom.ts";
import { packageFailure, REGISTRY_NAMES } from "./dependencies-model.ts";
import type { PackageFailure } from "./dependencies-model.ts";

/** The API keeps the newest releases up to this many (its response schema allows no more). */
export const RELEASE_LIST_CAP = 2000;
/** How many rows of the table are shown before "Show all". */
export const VISIBLE_ROWS = 100;

export const TIMELINE_HINT = "Use the left and right arrow keys on the strip, or the two buttons, to move between releases.";
export const MARK_RULES = "A hollow mark is a pre-release by its version number. A diamond is a version that was yanked or deprecated.";
export const NOT_A_JUDGEMENT = "Nothing here says whether a package is maintained, safe or worth using. It is a list of dates the registry recorded.";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"] as const;

function parts(timestamp: string): { year: number; month: string; day: number; hh: string; mm: string } | null {
  const at = new Date(timestamp);
  if (Number.isNaN(at.getTime())) return null;
  const month = MONTHS[at.getUTCMonth()];
  if (month === undefined) return null;
  return {
    year: at.getUTCFullYear(),
    month,
    day: at.getUTCDate(),
    hh: String(at.getUTCHours()).padStart(2, "0"),
    mm: String(at.getUTCMinutes()).padStart(2, "0"),
  };
}

/** "12 March 2014", as the UTC calendar day. The raw text when it is not a date. */
export function formatReleaseDay(timestamp: string): string {
  const p = parts(timestamp);
  return p === null ? timestamp : `${p.day} ${p.month} ${p.year}`;
}

/** "12 March 2014, 10:15 UTC". */
export function formatReleaseMoment(timestamp: string): string {
  const p = parts(timestamp);
  return p === null ? timestamp : `${p.day} ${p.month} ${p.year}, ${p.hh}:${p.mm} UTC`;
}

/** What a date in the list is, in the registry's own terms. Always shown with the history. */
export function describeDateMeaning(meaning: PackageReleases["date_meaning"], registry: PackageRegistry): string {
  const where = REGISTRY_NAMES[registry];
  return meaning === "first_file_upload"
    ? `Each date is the earliest time ${where} recorded a file being uploaded for that version. It is when the version reached ${where}, not necessarily the day its author tagged or announced it.`
    : `Each date is the publish time ${where} recorded for that version. It is when the version reached ${where}, not necessarily the day its author tagged or announced it.`;
}

const STATUS_SENTENCES: Readonly<Record<ReleaseEntry["status"], string | null>> = {
  normal: null,
  yanked: "Yanked: PyPI marks every file of this version as one not to install unless it is asked for by exact version.",
  deprecated: "Deprecated: the maintainers attached a deprecation notice to this version on npm.",
};

export const PRE_RELEASE_SENTENCE = "Pre-release by its version number. That is a reading of the number, not something the registry says.";

/** Short labels for the table's last column, in a fixed order. */
export function releaseNotes(entry: Pick<ReleaseEntry, "pre_release" | "status">): ReadonlyArray<string> {
  const notes: string[] = [];
  if (entry.pre_release) notes.push("pre-release by number");
  if (entry.status === "yanked") notes.push("yanked");
  if (entry.status === "deprecated") notes.push("deprecated");
  return notes;
}

/** One release in full, for the line under the strip and for assistive technology. */
export function describeRelease(entry: ReleaseEntry): string {
  const sentences = [`Version ${entry.version}, ${formatReleaseMoment(entry.released_at)}.`];
  if (entry.pre_release) sentences.push(PRE_RELEASE_SENTENCE);
  const status = STATUS_SENTENCES[entry.status];
  if (status !== null) sentences.push(status);
  return sentences.join(" ");
}

/** The short name a mark carries for assistive technology: version, day and notes. The full sentence is `describeRelease`. */
export function releaseLabel(entry: ReleaseEntry): string {
  return [entry.version, formatReleaseDay(entry.released_at), ...releaseNotes(entry)].join(", ");
}

/** What the strip is called as a whole. */
export function describeStrip(name: string, releases: ReadonlyArray<ReleaseEntry>): string {
  const first = releases[0];
  const newest = releases[releases.length - 1];
  const years = first && newest ? `, from ${formatReleaseDay(first.released_at).split(" ").pop()} to ${formatReleaseDay(newest.released_at).split(" ").pop()}` : "";
  return `Release dates of ${name}: ${plural(releases.length, "release", "releases")}${years}. ${TIMELINE_HINT}`;
}

/** Before the history is asked for: what pressing the button does, and what it costs the registry. */
export function describeLoadLead(name: string, registry: PackageRegistry): string {
  const where = REGISTRY_NAMES[registry];
  return `See when each version of ${name} reached ${where}, from ${where}'s own answer for the package. If VYRON still holds that answer from the last ten minutes it is reused, and nothing more is asked of ${where}.`;
}

export const describeLoadButton = (name: string): string => `Show release history for ${name}`;
export const describeLoading = (name: string): string => `Reading the release dates for ${name}…`;
export const describeUnreadableHistory = (name: string): string =>
  `${name} is given as a link, path or alias rather than a package name, so there is no registry answer to read release dates from.`;

/** The three marks the strip uses, with the words that go beside each. A hollow diamond is both rules at once. */
export const RELEASE_LEGEND: ReadonlyArray<{ readonly shape: "circle" | "diamond"; readonly hollow: boolean; readonly text: string }> = [
  { shape: "circle", hollow: false, text: "Release" },
  { shape: "circle", hollow: true, text: "Pre-release by its version number" },
  { shape: "diamond", hollow: false, text: "Yanked or deprecated" },
];

/** "Release 5 of 23". */
export function describePosition(index: number, count: number): string {
  return `Release ${index + 1} of ${count}`;
}

export interface ReleaseSummary {
  readonly count: number;
  readonly preReleases: number;
  readonly yanked: number;
  readonly deprecated: number;
  readonly text: string;
}

export function summarizeReleases(releases: ReadonlyArray<ReleaseEntry>): ReleaseSummary {
  const count = releases.length;
  const preReleases = releases.filter((entry) => entry.pre_release).length;
  const yanked = releases.filter((entry) => entry.status === "yanked").length;
  const deprecated = releases.filter((entry) => entry.status === "deprecated").length;
  const first = releases[0];
  const newest = releases[count - 1];
  if (first === undefined || newest === undefined) {
    return { count, preReleases, yanked, deprecated, text: "The registry's answer has no dated release, so there is nothing to draw." };
  }
  const bits = [`${plural(count, "release", "releases")}, from ${formatReleaseDay(first.released_at)} to ${formatReleaseDay(newest.released_at)}.`];
  if (preReleases > 0) bits.push(`${preReleases} ${preReleases === 1 ? "is a pre-release" : "are pre-releases"} by version number.`);
  if (yanked > 0) bits.push(`${yanked} ${yanked === 1 ? "is" : "are"} yanked.`);
  if (deprecated > 0) bits.push(`${deprecated} ${deprecated === 1 ? "is" : "are"} deprecated.`);
  return { count, preReleases, yanked, deprecated, text: bits.join(" ") };
}

const LEFT_OUT_WORDS: Readonly<Record<ReleaseLeftOut["kind"], (count: number) => string>> = {
  undated: (n) => `${plural(n, "version has", "versions have")} no usable date in the registry's answer, so ${n === 1 ? "it is" : "they are"} not on the strip`,
  removed: (n) => `${plural(n, "version", "versions")} ${n === 1 ? "has" : "have"} a recorded time but ${n === 1 ? "is" : "are"} no longer published on npm, so ${n === 1 ? "it is" : "they are"} not listed`,
  implausible_date: (n) => `${plural(n, "version", "versions")} ${n === 1 ? "carries" : "carry"} a date before 1991 or after the moment of the read, which cannot be right, so ${n === 1 ? "it is" : "they are"} left out`,
  unusable: (n) => `${plural(n, "entry", "entries")} ${n === 1 ? "is" : "are"} not ${n === 1 ? "a version" : "versions"} a registry would issue, so ${n === 1 ? "it is" : "they are"} left out`,
};

/** One sentence per kind of version the registry named that is not on the strip, in the API's order. */
export function describeReleaseLeftOut(items: ReadonlyArray<ReleaseLeftOut>): ReadonlyArray<string> {
  return items.map((item) => `${LEFT_OUT_WORDS[item.kind](item.count)}.`);
}

/** Said when the API dropped the earliest releases to keep the list to `RELEASE_LIST_CAP`; null when it dropped none. */
export function describeOmitted(omitted: number): string | null {
  if (omitted <= 0) return null;
  return `${plural(omitted, "earlier release", "earlier releases")} ${omitted === 1 ? "is" : "are"} not listed, because VYRON keeps the newest ${RELEASE_LIST_CAP}.`;
}

/** Said when marks overlap in the drawing, so the picture is not taken for a complete count; null when none do. */
export function describeCrowding(crowded: number): string | null {
  if (crowded <= 0) return null;
  return `${plural(crowded, "mark overlaps", "marks overlap")} another, because those releases are too close together to tell apart at this width. The table lists every release.`;
}

export interface ReleaseRow {
  /** Position in the API's list (oldest first), which is also the mark's index. */
  readonly index: number;
  readonly version: string;
  readonly date: string;
  readonly notes: ReadonlyArray<string>;
}

export interface ReleaseRows {
  readonly rows: ReadonlyArray<ReleaseRow>;
  /** Releases not in `rows`. */
  readonly hidden: number;
}

/** The table, newest first, cut to `limit` rows unless `all`. */
export function releaseRows(releases: ReadonlyArray<ReleaseEntry>, all: boolean, limit: number = VISIBLE_ROWS): ReleaseRows {
  const newestFirst: ReleaseRow[] = [];
  for (let index = releases.length - 1; index >= 0; index -= 1) {
    const entry = releases[index] as ReleaseEntry;
    newestFirst.push({ index, version: entry.version, date: formatReleaseDay(entry.released_at), notes: releaseNotes(entry) });
  }
  const shown = all ? newestFirst : newestFirst.slice(0, limit);
  return { rows: shown, hidden: newestFirst.length - shown.length };
}

/** The label of the button that shows or hides the rest of the table, or null when there is no rest to hide. */
export function describeShowAll(total: number, all: boolean, limit: number = VISIBLE_ROWS): string | null {
  if (total <= limit) return null;
  return all ? `Show only the newest ${limit}` : `Show all ${total} releases`;
}

const NO_DATES = /sent no release dates/i;

/**
 * Words for a release history that could not be read. The one answer that is about content, not about reaching the
 * registry, is a registry document with no dates in it; everything else is the same as for reading a package.
 */
export function releasesFailure(error: unknown, registry: PackageRegistry, name: string): PackageFailure {
  const e = (error ?? {}) as { readonly code?: unknown; readonly message?: unknown; readonly requestId?: unknown };
  const message = typeof e.message === "string" ? e.message : "";
  if (e.code === "upstream_error" && NO_DATES.test(message)) {
    const where = REGISTRY_NAMES[registry];
    const body =
      registry === "pypi"
        ? "PyPI's answer for this package has no release list. PyPI documents that list as deprecated, so it may not always be there. The dependencies on this page are not affected."
        : "npm's answer for this package has no publish times. The dependencies on this page are not affected.";
    return { title: `${where} sent no release dates for ${name}`, body, wait: null, requestId: typeof e.requestId === "string" ? e.requestId : null, retryable: false };
  }
  return packageFailure(error, registry, name);
}
