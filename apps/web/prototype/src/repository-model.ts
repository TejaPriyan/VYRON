/**
 * The repository page's logic with no DOM in it, so every sentence the page shows and every number it
 * derives can be tested. Nothing here fetches anything and nothing is invented: it turns what the API
 * returned (`RepositoryAnalysis`, `GitHubTarget`, an error) into words and figures.
 */
import type { CommitActivity, GitHubTarget, RepositoryActivity, RepositoryAnalysis } from "../../../../packages/types/src/api.ts";
import { describeWait, errorState } from "../../../../packages/ui/src/states.ts";
import type { ErrorCodeKey } from "../../../../packages/ui/src/states.ts";
import { formatEventDate } from "./format.ts";

// ---- what to analyse ---------------------------------------------------------------------------

export interface RepoRequest {
  readonly owner: string;
  readonly repo: string;
  /** Said to the reader when the link was to something inside the repository, else null. */
  readonly note: string | null;
}

export type RequestFromLink = { readonly request: RepoRequest } | { readonly problem: string };

const INSIDE: Readonly<Record<string, (target: GitHubTarget) => string>> = {
  issue: (t) => `issue #${t.number ?? "?"}`,
  pull_request: (t) => `pull request #${t.number ?? "?"}`,
  commit: () => "a commit",
  release: () => "a release",
  tree: () => "a folder",
  blob: () => "a file",
};

/** The repository a parsed GitHub link belongs to. A link to an account alone is not a repository. */
export function requestFromLink(target: GitHubTarget): RequestFromLink {
  if (target.kind === "owner" || target.repo === null) {
    return { problem: `That link points at the account ${target.owner}, not a repository. Paste a link like github.com/${target.owner}/name.` };
  }
  const inside = INSIDE[target.kind];
  const note = inside ? `Analyzing ${target.owner}/${target.repo}, the repository that ${inside(target)} belongs to.` : null;
  return { request: { owner: target.owner, repo: target.repo, note } };
}

/** `?repo=owner/name` -> the text to analyse, or null. Anything else in the query string is ignored. */
export function readRepoParam(search: string): string | null {
  const value = new URLSearchParams(search).get("repo");
  return value !== null && value.trim() !== "" ? value.trim().slice(0, 300) : null;
}

export function writeRepoParam(fullName: string | null): string {
  if (fullName === null) return "";
  const params = new URLSearchParams();
  params.set("repo", fullName);
  return `?${params.toString()}`;
}

// ---- when and how fresh ------------------------------------------------------------------------

/** "just now", "12 seconds ago", "4 minutes ago", "2 hours ago", "3 days ago". */
export function describeAge(timestamp: string, nowMs: number): string {
  const then = Date.parse(timestamp);
  if (!Number.isFinite(then)) return "at an unknown time";
  const seconds = Math.max(0, Math.floor((nowMs - then) / 1000));
  const unit = (n: number, one: string): string => `${n} ${one}${n === 1 ? "" : "s"} ago`;
  if (seconds < 5) return "just now";
  if (seconds < 60) return unit(seconds, "second");
  if (seconds < 3600) return unit(Math.floor(seconds / 60), "minute");
  if (seconds < 86_400) return unit(Math.floor(seconds / 3600), "hour");
  return unit(Math.floor(seconds / 86_400), "day");
}

export interface CacheNote {
  /** One or two words for the badge. */
  readonly label: string;
  /** A whole sentence saying how old the figures are and why. */
  readonly text: string;
  /** "warn" when the figures may be out of date. */
  readonly tone: "ok" | "warn";
}

/**
 * Says how current the analysis is. `fetched_at` is when GitHub last said these values were right, so
 * a "stale" copy shows its real age rather than the time it was served.
 */
export function describeCache(
  status: RepositoryAnalysis["cache_status"] | RepositoryActivity["cache_status"] | CommitActivity["cache_status"],
  fetchedAt: string,
  nowMs: number,
): CacheNote {
  const age = describeAge(fetchedAt, nowMs);
  switch (status) {
    case "fresh":
      return { label: "Live", text: `Read from GitHub ${age}.`, tone: "ok" };
    case "revalidated":
      return { label: "Confirmed", text: `Checked with GitHub ${age}; nothing had changed since the previous read.`, tone: "ok" };
    case "stale":
      return { label: "Stale", text: `GitHub could not be reached, so these figures are from ${age} and may be out of date.`, tone: "warn" };
  }
}

/**
 * What the page says about a licence, from exactly what GitHub reported. A standard identifier is shown as it
 * came. GitHub can also find a licence file and call it "Other" with no identifier (torvalds/linux, seen on
 * 2026-11-02); that is shown as GitHub's name plus the fact that no standard identifier came with it. Only when
 * GitHub reports no licence at all does the page say so, and then it says what GitHub did, not what the
 * repository's authors did: "declared" would claim more than GitHub says.
 */
export function describeLicense(analysis: Pick<RepositoryAnalysis, "license_spdx" | "license_name">): string {
  if (analysis.license_spdx) return `License ${analysis.license_spdx}`;
  if (analysis.license_name) return `License ${analysis.license_name} (no standard identifier)`;
  return "No license identified by GitHub";
}

// ---- figures -----------------------------------------------------------------------------------

const GROUPED = new Intl.NumberFormat("en-US");

export function formatCount(n: number): string {
  return GROUPED.format(n);
}

/** Sizes in binary units, to one decimal place above a kibibyte. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"] as const;
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(1)} ${units[index]}`;
}

/** "4 Mar 2018" from a GitHub timestamp (UTC date), or the given words when there is none. */
export function formatDay(timestamp: string | null, whenMissing: string): string {
  if (timestamp === null) return whenMissing;
  return formatEventDate(timestamp.slice(0, 10), "day");
}

export interface LanguageRow {
  readonly name: string;
  readonly bytes: number;
  /** 0 to 1. */
  readonly share: number;
  /** `share` as a percentage rounded to one decimal place. */
  readonly percent: number;
}

/**
 * A row's share for display. One decimal place, except that a language with bytes but under 0.05% reads
 * "<0.1%" rather than "0.0%", which would say it has none.
 */
export function formatPercent(row: LanguageRow): string {
  return row.share > 0 && row.percent === 0 ? "<0.1%" : `${row.percent.toFixed(1)}%`;
}

export interface LanguageBreakdown {
  readonly rows: ReadonlyArray<LanguageRow>;
  /** Languages folded into `other`, 0 when nothing was. */
  readonly otherCount: number;
  readonly other: LanguageRow | null;
  readonly totalBytes: number;
}

/**
 * Languages by bytes of code, largest first (ties by name), the smallest folded into "Other" past
 * `maxRows`. A language with no bytes is left out. Percentages are of GitHub's byte counts, which
 * measure files by extension, not effort or importance.
 */
export function languageBreakdown(languages: Readonly<Record<string, number>>, maxRows = 8): LanguageBreakdown {
  const present = Object.entries(languages)
    .filter(([, bytes]) => Number.isFinite(bytes) && bytes > 0)
    .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const totalBytes = present.reduce((sum, [, bytes]) => sum + bytes, 0);
  const row = (name: string, bytes: number): LanguageRow => {
    const share = totalBytes === 0 ? 0 : bytes / totalBytes;
    return { name, bytes, share, percent: Math.round(share * 1000) / 10 };
  };
  const limit = Math.max(2, maxRows); // room for at least one named language and "Other"
  const shown = present.length > limit ? present.slice(0, limit - 1) : present;
  const folded = present.length > limit ? present.slice(limit - 1) : [];
  const otherBytes = folded.reduce((sum, [, bytes]) => sum + bytes, 0);
  return {
    rows: shown.map(([name, bytes]) => row(name, bytes)),
    otherCount: folded.length,
    other: folded.length > 0 ? row("Other", otherBytes) : null,
    totalBytes,
  };
}

// ---- contributors and the latest release (the opt-in second read) ---------------------------------

/** Said next to the button, so nobody spends GitHub's quota without being told what it costs. */
export const ACTIVITY_COST =
  "This asks GitHub twice more, or three times if the figures above are over a minute old. It only happens when you ask.";

/** GitHub's own definition, repeated wherever a release is shown, because "latest" is easy to misread. */
export const LATEST_RELEASE_NOTE =
  "GitHub's latest release is the most recent release that is not a draft or a pre-release, ordered by its created date (the date of the commit the release uses). It is not necessarily the highest version number.";

export const CONTRIBUTORS_NOTE =
  "Commits GitHub attributes to each account, with up to ten accounts shown. A commit count says nothing about the quality or importance of the work.";

export interface ContributorRow {
  readonly login: string;
  /** "1,204 commits", "1 commit". */
  readonly commits: string;
  /** A github.com address built by the API, or null (bots, and logins GitHub's own naming rules do not cover). */
  readonly profileUrl: string | null;
  readonly isBot: boolean;
}

export interface ReleaseView {
  /** The tag, which is what identifies the release. */
  readonly tag: string;
  /** GitHub's title for the release when it differs from the tag, else null. */
  readonly name: string | null;
  readonly published: string;
  readonly prerelease: boolean;
  readonly url: string;
}

export interface ActivityView {
  readonly rows: ReadonlyArray<ContributorRow>;
  /** A sentence saying why there is no list, or null when there is one. */
  readonly noContributors: string | null;
  readonly release: ReleaseView | null;
  /** A sentence saying there is no release, or null when there is one. */
  readonly noRelease: string | null;
  /** One line for a screen reader when the section loads. */
  readonly summary: string;
}

const NO_CONTRIBUTORS: Readonly<Record<"none" | "declined", string>> = {
  none: "GitHub lists no contributors for this repository.",
  declined:
    "GitHub declined to list this repository's contributors. GitHub documents that it does this when a repository's history or contributor list is too large.",
};

const NO_RELEASE =
  "GitHub reports no published release for this repository. It does not count drafts or pre-releases, and a tag on its own is not a release.";

/** Turns the API's answer into the words and figures the section shows. Nothing is added to what GitHub said. */
export function describeActivity(activity: RepositoryActivity): ActivityView {
  const rows = activity.contributors.map(
    (person): ContributorRow => ({
      login: person.login,
      commits: `${formatCount(person.contributions)} commit${person.contributions === 1 ? "" : "s"}`,
      profileUrl: person.profile_url,
      isBot: person.is_bot,
    }),
  );
  const found = activity.latest_release;
  const release: ReleaseView | null =
    found === null
      ? null
      : {
          tag: found.tag_name,
          name: found.name !== null && found.name !== found.tag_name ? found.name : null,
          published: formatDay(found.published_at, "Publish date not given"),
          prerelease: found.prerelease,
          url: found.html_url,
        };
  const noContributors = activity.contributors_status === "listed" ? null : NO_CONTRIBUTORS[activity.contributors_status];
  const top = rows[0];
  const people =
    top === undefined
      ? "no contributors listed"
      : `${rows.length} contributor${rows.length === 1 ? "" : "s"} listed, most commits ${top.login}`;
  const latest = release === null ? "no published release" : `latest release ${release.tag}`;
  return {
    rows,
    noContributors,
    release,
    noRelease: release === null ? NO_RELEASE : null,
    summary: `${activity.full_name}: ${people}; ${latest}.`,
  };
}

// ---- commit activity (the opt-in third read) ---------------------------------------------------------

/** Said next to the button. The first ask can only be answered "still computing", so that is said before it happens. */
export const COMMIT_ACTIVITY_COST =
  "This asks GitHub once more, or twice if the figures above are over a minute old. GitHub works commit activity out in the background, so the first ask can come back as \"still computing\". It only happens when you ask.";

export const COMMIT_ACTIVITY_NOTE =
  "Commits on the default branch, counted by GitHub for each week from Sunday (UTC). Bars are scaled to the busiest week. The last bar is the current week and is still filling. A commit count says nothing about the quality or importance of the work.";

export const COMMIT_ACTIVITY_DERIVED =
  "Totals and the busiest week are added up by VYRON from GitHub's weekly counts. The weekly counts are GitHub's.";

export const COMPUTING_TITLE = "GitHub is still working this out";
export const COMPUTING_BODY =
  "GitHub computes commit activity in the background and had not finished when VYRON asked. Check again in a few seconds. Each check uses one of GitHub's requests, and VYRON never checks on its own.";
export const NO_COMMIT_ACTIVITY = "GitHub reports no commit activity for this repository.";

export interface CommitBar {
  /** Commits that week. */
  readonly total: number;
  /** 0 to 1, as a share of the busiest week. A week with commits is never drawn as zero. */
  readonly height: number;
  /** True for a week that had not ended when GitHub was asked, so its bar is a count so far. */
  readonly inProgress: boolean;
  /** "Week of 9 Nov 2025: 12 commits", for a hover and a screen reader. */
  readonly label: string;
}

export interface CommitTableRow {
  readonly week: string;
  readonly commits: string;
  readonly inProgress: boolean;
}

export interface CommitActivityView {
  readonly state: CommitActivity["status"];
  readonly bars: ReadonlyArray<CommitBar>;
  readonly rows: ReadonlyArray<CommitTableRow>;
  /** "2,431 commits in 52 weeks", added up by VYRON; null unless there are weeks. */
  readonly totalLine: string | null;
  /** "2,431": the same sum as a bare figure for a ledger row, with its label in `spanLabel`. */
  readonly totalFigure: string | null;
  /** "Commits in 52 weeks". */
  readonly spanLabel: string;
  /** "182 commits, week of 3 Mar 2026"; null when there are no weeks or no commits in any of them. */
  readonly busiest: string | null;
  /** How many of the weeks had no commits at all. */
  readonly quietWeeks: number;
  /** "9 Nov 2025" and "1 Nov 2026": the first and last week, for the axis. */
  readonly firstWeek: string | null;
  readonly lastWeek: string | null;
  /** One line for a screen reader when the section loads. */
  readonly summary: string;
}

const WEEK_MS = 7 * 86_400_000;

const commits = (n: number): string => `${formatCount(n)} commit${n === 1 ? "" : "s"}`;

/**
 * Turns the API's answer into the figures and words the chart shows. Nothing is invented: a week keeps GitHub's
 * count, the sums are plain additions, and "in progress" is decided from the time GitHub was asked (`fetched_at`),
 * never from the clock of the machine reading the page.
 */
export function describeCommitActivity(activity: CommitActivity): CommitActivityView {
  const asked = Date.parse(activity.fetched_at);
  const peak = activity.weeks.reduce((most, week) => Math.max(most, week.total), 0);
  const bars = activity.weeks.map((week): CommitBar => {
    const start = Date.parse(week.week_start);
    const inProgress = Number.isFinite(asked) && Number.isFinite(start) && asked < start + WEEK_MS;
    const day = formatDay(week.week_start, "an unknown date");
    return {
      total: week.total,
      height: week.total === 0 || peak === 0 ? 0 : Math.max(0.03, week.total / peak),
      inProgress,
      label: `Week of ${day}: ${commits(week.total)}${inProgress ? " so far" : ""}`,
    };
  });
  const rows = activity.weeks.map(
    (week, index): CommitTableRow => ({
      week: formatDay(week.week_start, "an unknown date"),
      commits: formatCount(week.total),
      inProgress: bars[index]?.inProgress ?? false,
    }),
  );
  const sum = activity.weeks.reduce((all, week) => all + week.total, 0);
  const busiestWeek = activity.weeks.find((week) => week.total === peak);
  const first = activity.weeks[0];
  const last = activity.weeks[activity.weeks.length - 1];
  const hasWeeks = activity.weeks.length > 0 && first !== undefined && last !== undefined;
  const weeksText = `${activity.weeks.length} week${activity.weeks.length === 1 ? "" : "s"}`;
  const totalLine = hasWeeks ? `${commits(sum)} in ${weeksText}` : null;
  // With no commits at all there is no busiest week to name: every week ties at zero.
  const busiest = hasWeeks && peak > 0 && busiestWeek ? `${commits(peak)}, week of ${formatDay(busiestWeek.week_start, "an unknown date")}` : null;
  const quietWeeks = activity.weeks.filter((week) => week.total === 0).length;
  let summary: string;
  if (activity.status === "computing") summary = `${activity.full_name}: GitHub is still computing commit activity. Check again in a few seconds.`;
  else if (activity.status === "none" || !hasWeeks) summary = `${activity.full_name}: GitHub reports no commit activity.`;
  else {
    const quiet = `${quietWeeks} week${quietWeeks === 1 ? "" : "s"} with no commits`;
    summary = `${activity.full_name}: ${totalLine}. ${busiest === null ? "" : `Busiest week ${busiest}. `}${quiet}.`;
  }
  return {
    state: activity.status,
    bars,
    rows,
    totalLine,
    totalFigure: hasWeeks ? formatCount(sum) : null,
    spanLabel: `Commits in ${weeksText}`,
    busiest,
    quietWeeks,
    firstWeek: hasWeeks ? formatDay(first.week_start, "an unknown date") : null,
    lastWeek: hasWeeks ? formatDay(last.week_start, "an unknown date") : null,
    summary,
  };
}

// ---- failures ----------------------------------------------------------------------------------

export interface RepoFailure {
  readonly title: string;
  readonly body: string;
  readonly wait: string | null;
  readonly requestId: string | null;
  readonly retryable: boolean;
}

interface Thrown {
  readonly code?: unknown;
  readonly message?: unknown;
  readonly requestId?: unknown;
  readonly retryAfterSeconds?: unknown;
  readonly retryable?: unknown;
}

const sentence = (text: string): string => {
  const trimmed = text.trim();
  if (trimmed === "") return "";
  const capital = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
};

/**
 * Words for a failed analysis. The API's own message is used where it is specific and safe (it never
 * repeats GitHub's text or a credential); the shared table covers any code not handled here.
 */
export function repositoryFailure(error: unknown, requested: string): RepoFailure {
  const e = (error ?? {}) as Thrown;
  const code = typeof e.code === "string" ? e.code : "network_error";
  const message = typeof e.message === "string" ? e.message : "";
  const requestId = typeof e.requestId === "string" ? e.requestId : null;
  const retryAfter = typeof e.retryAfterSeconds === "number" ? e.retryAfterSeconds : null;
  const base = { wait: null, requestId: null, retryable: false } as const;

  if (code === "not_found") {
    if (/renamed or moved/i.test(message)) {
      return { ...base, title: `${requested} has moved`, body: "GitHub says this repository was renamed or moved. Look up its current name and paste that." };
    }
    return {
      ...base,
      title: `No public repository called ${requested}`,
      body: "GitHub has no public repository with that name. VYRON reads public repositories only, so a private one looks the same as a missing one.",
    };
  }
  if (code === "validation_error") {
    return { ...base, title: "That is not a repository name", body: "Use owner/name, like vercel/next.js, or paste a link to a GitHub repository." };
  }
  if (code === "upstream_rate_limited") {
    return {
      title: "GitHub's rate limit was reached",
      body: "VYRON reads GitHub with a limited quota, and it is used up. A server token raises the limit from 60 to 5,000 requests an hour.",
      wait: describeWait(retryAfter),
      requestId: null,
      retryable: true,
    };
  }
  if (code === "upstream_error") {
    return { title: "GitHub did not give a usable answer", body: sentence(message), wait: null, requestId, retryable: true };
  }
  if (code === "service_unavailable" && /switched off/i.test(message)) {
    return { ...base, title: "GitHub access is switched off", body: "This server was started without GitHub access. Restart it without --no-github to analyze repositories." };
  }
  const shared = errorState(code as ErrorCodeKey) ?? errorState("network_error");
  return {
    title: shared.title,
    body: shared.body,
    wait: describeWait(retryAfter),
    requestId: shared.showRequestId ? requestId : null,
    retryable: e.retryable === true || shared.action === "retry",
  };
}

// ---- the quota line ----------------------------------------------------------------------------

export interface QuotaLine {
  readonly text: string;
  /** True when what is left is a tenth of the limit or less. */
  readonly low: boolean;
}

/** "58 of 60 GitHub requests left until 11:12 UTC", or null when GitHub has not said. */
export function describeQuota(quota: { limit: number | null; remaining: number | null; reset_at: string | null }): QuotaLine | null {
  if (quota.limit === null || quota.remaining === null) return null;
  const until = quota.reset_at === null ? "" : ` until ${quota.reset_at.slice(11, 16)} UTC`;
  return {
    text: `${formatCount(quota.remaining)} of ${formatCount(quota.limit)} GitHub requests left${until}`,
    low: quota.limit > 0 && quota.remaining <= quota.limit / 10,
  };
}

/** Suggest a registry lookup matching a repository's primary language (never claims the package exists). */
export function suggestedPackageForRepo(
  analysis: Pick<RepositoryAnalysis, "full_name" | "primary_language">,
): { readonly registry: "pypi" | "npm"; readonly name: string } | null {
  const repo = analysis.full_name.split("/")[1]?.trim() ?? "";
  if (repo === "") return null;
  const lang = (analysis.primary_language ?? "").toLowerCase();
  if (lang === "python") return { registry: "pypi", name: repo.toLowerCase() };
  if (lang === "javascript" || lang === "typescript") return { registry: "npm", name: repo.toLowerCase() };
  return null;
}
