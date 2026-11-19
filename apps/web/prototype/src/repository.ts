/**
 * The repository page: asks the same-origin API to read a public GitHub repository now and shows what
 * came back, where it came from and how old it is. Every value is drawn as text (never markup), and the
 * page keeps nothing: no storage, no cookies. The address (`?repo=owner/name`) is the only state.
 */
import { VyronClient } from "../../../../packages/api-client/src/client.ts";
import type { CommitActivity, RateLimitStatus, RepositoryActivity, RepositoryAnalysis } from "../../../../packages/types/src/api.ts";
import { clear, fill, h, s } from "./dom.ts";
import { downloadJson } from "./export.ts";
import { brandMark } from "./icons.ts";
import { provenanceBadge } from "./marks.ts";
import {
  ACTIVITY_COST,
  COMMIT_ACTIVITY_COST,
  COMMIT_ACTIVITY_DERIVED,
  COMMIT_ACTIVITY_NOTE,
  COMPUTING_BODY,
  COMPUTING_TITLE,
  CONTRIBUTORS_NOTE,
  LATEST_RELEASE_NOTE,
  NO_COMMIT_ACTIVITY,
  describeActivity,
  describeCommitActivity,
  describeCache,
  describeLicense,
  describeQuota,
  formatBytes,
  formatCount,
  formatDay,
  formatPercent,
  languageBreakdown,
  readRepoParam,
  repositoryFailure,
  requestFromLink,
  suggestedPackageForRepo,
  writeRepoParam,
} from "./repository-model.ts";
import type { CommitActivityView, LanguageRow, RepoFailure } from "./repository-model.ts";

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`#${id} is missing from repository.html`);
  return node as T;
}

/** The analysis reads two GitHub resources and may retry once, so allow longer than the map's calls. */
const client = new VyronClient({ baseUrl: "", timeoutMs: 30_000 });
const reading = el<HTMLElement>("reading");
const form = el<HTMLFormElement>("repo-form");
const input = el<HTMLInputElement>("repo-input");
const submit = el<HTMLButtonElement>("repo-submit");
const quotaLine = el<HTMLElement>("quota");
const live = el<HTMLElement>("live");
const EXAMPLES = ["pallets/flask", "vercel/next.js", "rust-lang/rust"] as const;
const AGE_REFRESH_MS = 10_000;

fill(el("brand-mark"), brandMark());

let generation = 0;
let controller: AbortController | null = null;
let ageTimer: number | null = null;
/** Redraw the "how old" text of whatever is on the page. Emptied whenever the reading is replaced. */
let agePainters: Array<() => void> = [];

function announce(message: string): void {
  live.textContent = message;
}

function stopAgeTimer(): void {
  if (ageTimer !== null) window.clearInterval(ageTimer);
  ageTimer = null;
  agePainters = [];
}

/** Replace the reading and, when asked, move focus to its heading so a screen reader starts there. */
function show(content: HTMLElement, focus: boolean): void {
  clear(reading);
  reading.append(content);
  if (focus) content.querySelector<HTMLElement>("[tabindex='-1']")?.focus({ preventScroll: false });
  reading.scrollTop = 0;
  document.getElementById("repo-main")?.scrollTo({ top: 0 });
}

/** A link only when it is an http(s) address, so a surprising value can never become a script URL. */
function safeLink(href: string, text: string, className?: string): HTMLElement {
  const web = /^https?:\/\//i.test(href);
  return web ? h("a", { href, rel: "noopener noreferrer", ...(className ? { class: className } : {}) }, text) : h("span", {}, text);
}

// ---- states --------------------------------------------------------------------------------------

function renderIdle(): void {
  const examples = EXAMPLES.flatMap((name, index) => {
    const button = h("button", { type: "button", class: "text-button example", "data-example": name }, name);
    button.addEventListener("click", () => void analyze(name, "push"));
    return [button, index < EXAMPLES.length - 2 ? ", " : index === EXAMPLES.length - 2 ? " or " : "."];
  });
  show(
    h(
      "div",
      { class: "idle" },
      h("h1", { class: "idle-title", id: "reading-title", tabindex: "-1" }, "Read a repository"),
      h(
        "p",
        { class: "idle-lede" },
        "Paste a GitHub link or owner/name above. VYRON asks GitHub for the repository when you do, shows what GitHub said and how old it is, and keeps nothing.",
      ),
      h("p", { class: "idle-examples" }, "Try ", ...examples),
    ),
    false,
  );
  announce("");
}

function renderLoading(label: string): void {
  show(h("p", { class: "state-line", role: "status" }, `Reading ${label} from GitHub…`), false);
  announce(`Reading ${label} from GitHub`);
}

function renderFailure(failure: RepoFailure, retry: (() => void) | null, kind: "alert" | "status" = "alert"): void {
  const retryButton = retry && failure.retryable ? h("button", { type: "button", class: "text-button" }, "Try again") : null;
  retryButton?.addEventListener("click", retry as () => void);
  show(
    h(
      "div",
      { class: "failure", role: kind },
      h("h1", { class: "failure-title", id: "reading-title", tabindex: "-1" }, failure.title),
      failure.body ? h("p", { class: "failure-body" }, failure.body) : null,
      failure.wait ? h("p", { class: "dust" }, failure.wait) : null,
      failure.requestId ? h("p", { class: "dust small" }, `Request ${failure.requestId}.`) : null,
      retryButton,
    ),
    true,
  );
  announce(`${failure.title}. ${failure.body}`);
}

// ---- the report ----------------------------------------------------------------------------------

function ledgerRow(label: string, value: string, hint?: string): HTMLElement {
  return h("div", {}, h("dt", {}, label, hint ? h("span", { class: "hint" }, hint) : null), h("dd", {}, value));
}

function barFor(row: LanguageRow, other: boolean): SVGSVGElement {
  return s(
    "svg",
    { class: "share-bar", viewBox: "0 0 100 6", preserveAspectRatio: "none", "aria-hidden": "true", focusable: "false" },
    s("rect", { class: "share-track", x: 0, y: 0, width: 100, height: 6 }),
    s("rect", { class: other ? "share-fill is-other" : "share-fill", x: 0, y: 0, width: Math.max(0, Math.min(100, row.share * 100)), height: 6 }),
  );
}

function languageSection(languages: Readonly<Record<string, number>>): HTMLElement {
  const breakdown = languageBreakdown(languages);
  const heading = h("h2", { class: "small-heading", id: "languages-title" }, "Languages");
  if (breakdown.rows.length === 0 && breakdown.other === null) {
    return h("section", { "aria-labelledby": "languages-title" }, heading, h("p", { class: "dust" }, "GitHub reports no languages for this repository."));
  }
  const line = (row: LanguageRow, label: string, other: boolean): HTMLElement =>
    h(
      "tr",
      {},
      h("th", { scope: "row" }, label),
      h("td", { class: "bar-cell" }, barFor(row, other)),
      h("td", { class: "num" }, formatPercent(row)),
      h("td", { class: "num size dust" }, formatBytes(row.bytes)),
    );
  const rows = breakdown.rows.map((row) => line(row, row.name, false));
  if (breakdown.other) rows.push(line(breakdown.other, `Other (${breakdown.otherCount} languages)`, true));
  return h(
    "section",
    { "aria-labelledby": "languages-title" },
    heading,
    h(
      "table",
      { class: "langs" },
      h("caption", { class: "sr-only" }, "Languages by bytes of code"),
      h("thead", { class: "sr-only" }, h("tr", {}, h("th", { scope: "col" }, "Language"), h("th", { scope: "col" }, "Share"), h("th", { scope: "col" }, "Percent"), h("th", { scope: "col" }, "Size"))),
      h("tbody", {}, ...rows),
    ),
    h("p", { class: "note" }, "Shares are of the bytes of code GitHub counts by file type. They say nothing about effort or importance."),
  );
}

// ---- contributors and the latest release (opt-in) ----------------------------------------------------

/** One account's row. The link is the API's own github.com address and is only used as a link if it is http(s). */
function contributorRow(row: ReturnType<typeof describeActivity>["rows"][number]): HTMLElement {
  const name = row.profileUrl ? safeLink(row.profileUrl, row.login, "text-link") : h("span", {}, row.login);
  return h(
    "tr",
    {},
    h("th", { scope: "row" }, name, row.isBot ? h("span", { class: "bot-mark" }, "bot") : null),
    h("td", { class: "num" }, row.commits),
  );
}

/** A failure inside one of the opt-in sections: the report above it stays, and a retryable one offers to try again. */
function sectionFailure(failure: RepoFailure, retry: () => void): HTMLElement {
  const again = failure.retryable ? h("button", { type: "button", class: "text-button" }, "Try again") : null;
  again?.addEventListener("click", retry);
  return h(
    "div",
    { class: "failure", role: "alert" },
    h("p", { class: "failure-title" }, failure.title),
    failure.body ? h("p", { class: "failure-body" }, failure.body) : null,
    failure.wait ? h("p", { class: "dust" }, failure.wait) : null,
    failure.requestId ? h("p", { class: "dust small" }, `Request ${failure.requestId}.`) : null,
    again,
  );
}

function activityResult(activity: RepositoryActivity, repeat: () => void): HTMLElement {
  const view = describeActivity(activity);
  const ageText = h("span", { class: "age", "aria-live": "off" });
  const cacheLabel = h("span", { class: "cache-label" });
  const band = h("p", { class: "band-line activity-band" }, provenanceBadge(activity.provenance), cacheLabel, ageText);
  const paint = (): void => {
    const cache = describeCache(activity.cache_status, activity.fetched_at, Date.now());
    cacheLabel.textContent = cache.label;
    ageText.textContent = cache.text;
    band.setAttribute("data-tone", cache.tone);
  };
  paint();
  agePainters.push(paint);

  const people =
    view.rows.length > 0
      ? h(
          "table",
          { class: "people" },
          h("caption", { class: "sr-only" }, "Contributors by commits GitHub attributes to them"),
          h("thead", { class: "sr-only" }, h("tr", {}, h("th", { scope: "col" }, "Account"), h("th", { scope: "col" }, "Commits"))),
          h("tbody", {}, ...view.rows.map(contributorRow)),
        )
      : h("p", { class: "dust" }, view.noContributors ?? "");
  const release = view.release;
  const releaseBlock = release
    ? h(
        "div",
        { class: "release" },
        h("p", { class: "release-tag" }, release.tag, release.prerelease ? h("span", { class: "bot-mark" }, "pre-release") : null),
        release.name ? h("p", { class: "release-name" }, release.name) : null,
        h("p", { class: "dust" }, `Published ${release.published}`),
        h("p", { class: "head-links" }, safeLink(release.url, "View release on GitHub", "text-link")),
      )
    : h("p", { class: "dust" }, view.noRelease ?? "");

  const again = h("button", { type: "button", class: "text-button" }, "Check again");
  again.addEventListener("click", repeat);
  return h(
    "div",
    { class: "activity-result" },
    band,
    h(
      "div",
      { class: "activity-grid" },
      h("div", {}, h("h3", { class: "small-heading" }, "Top contributors"), people, view.rows.length > 0 ? h("p", { class: "note" }, CONTRIBUTORS_NOTE) : null),
      h("div", {}, h("h3", { class: "small-heading" }, "Latest release"), releaseBlock, h("p", { class: "note" }, LATEST_RELEASE_NOTE)),
    ),
    h("ul", { class: "evidence", "aria-label": "Read from" }, ...activity.source_urls.map((url) => h("li", {}, safeLink(url, url)))),
    h("p", { class: "head-links" }, again),
  );
}

/**
 * The section that reads contributors and the latest release. It asks nothing until the button is pressed,
 * because each press costs GitHub requests the basic reading does not; the cost is said beside the button.
 * It belongs to one report: reading another repository replaces it, and a request still in flight is dropped.
 */
function activitySection(analysis: RepositoryAnalysis, signal: AbortSignal, token: number): HTMLElement {
  const [owner = "", repo = ""] = analysis.full_name.split("/", 2);
  const heading = h("h2", { class: "small-heading", id: "activity-title", tabindex: "-1" }, "Contributors and latest release");
  const body = h("div", { class: "activity-body" });
  const section = h("section", { class: "activity", "aria-labelledby": "activity-title" }, heading, body);

  const prompt = (): void => {
    const ask = h("button", { type: "button", class: "primary", "aria-describedby": "activity-cost" }, "Load contributors and latest release");
    ask.addEventListener("click", () => void load());
    clear(body);
    fill(body, h("p", { class: "note", id: "activity-cost" }, ACTIVITY_COST), h("p", {}, ask));
    body.removeAttribute("aria-busy");
  };

  const load = async (): Promise<void> => {
    clear(body);
    fill(body, h("p", { class: "state-line", role: "status" }, "Asking GitHub for contributors and the latest release…"));
    body.setAttribute("aria-busy", "true");
    announce(`Reading contributors and the latest release of ${analysis.full_name} from GitHub`);
    try {
      const activity = await client.get("get_repository_activity", { path: { owner, repo }, signal });
      if (token !== generation) return;
      clear(body);
      fill(body, activityResult(activity, () => void load()));
      body.removeAttribute("aria-busy");
      heading.focus({ preventScroll: true });
      section.scrollIntoView({ block: "nearest" }); // the result sits below the fold; bring it into view without animation
      announce(describeActivity(activity).summary);
      void refreshQuota(signal, token); // this read spent some of GitHub's quota
    } catch (error) {
      if (token !== generation || (error as { code?: unknown } | null)?.code === "aborted") return;
      const failure = repositoryFailure(error, analysis.full_name);
      clear(body);
      fill(body, sectionFailure(failure, () => void load()));
      body.removeAttribute("aria-busy");
      announce(`${failure.title}. ${failure.body}`);
      void refreshQuota(signal, token);
    }
  };

  prompt();
  return section;
}

// ---- commit activity (opt-in) ------------------------------------------------------------------------

const BAR_SLOT = 10;
const BAR_GAP = 2;
const CHART_HEIGHT = 100;

/** 52 thin bars scaled to the busiest week. No text is drawn in the SVG, so it can stretch to any width. */
function commitChart(view: CommitActivityView): SVGSVGElement {
  const width = view.bars.length * BAR_SLOT;
  const usable = CHART_HEIGHT - 1; // the last unit is the baseline
  const bars = view.bars.map((bar, index) => {
    const height = bar.height * usable;
    return s(
      "rect",
      { class: bar.inProgress ? "commit-bar is-open" : "commit-bar", x: index * BAR_SLOT + BAR_GAP / 2, y: usable - height, width: BAR_SLOT - BAR_GAP, height },
      s("title", {}, bar.label),
    );
  });
  return s(
    "svg",
    { class: "commit-chart", viewBox: `0 0 ${width} ${CHART_HEIGHT}`, preserveAspectRatio: "none", role: "img", "aria-label": view.summary, focusable: "false" },
    ...bars,
    s("rect", { class: "commit-base", x: 0, y: usable, width, height: 1 }),
  );
}

function commitResult(activity: CommitActivity, repeat: () => void): { node: HTMLElement; focus: (fallback: HTMLElement) => void } {
  const view = describeCommitActivity(activity);
  const ageText = h("span", { class: "age", "aria-live": "off" });
  const cacheLabel = h("span", { class: "cache-label" });
  const band = h("p", { class: "band-line activity-band" }, provenanceBadge(activity.provenance), cacheLabel, ageText);
  const paint = (): void => {
    const cache = describeCache(activity.cache_status, activity.fetched_at, Date.now());
    cacheLabel.textContent = cache.label;
    ageText.textContent = cache.text;
    band.setAttribute("data-tone", cache.tone);
  };
  paint();
  agePainters.push(paint);

  const again = h("button", { type: "button", class: view.state === "computing" ? "primary" : "text-button" }, "Check again");
  again.addEventListener("click", repeat);
  const evidence = h("ul", { class: "evidence", "aria-label": "Read from" }, ...activity.source_urls.map((url) => h("li", {}, safeLink(url, url))));

  let main: HTMLElement;
  if (view.state === "computing") {
    main = h("div", { class: "computing", role: "status" }, h("p", { class: "failure-title" }, COMPUTING_TITLE), h("p", { class: "failure-body" }, COMPUTING_BODY), h("p", {}, again));
  } else if (view.state === "none" || view.totalLine === null) {
    main = h("p", { class: "dust" }, NO_COMMIT_ACTIVITY);
  } else {
    main = h(
      "div",
      { class: "commit-body" },
      h(
        "dl",
        { class: "ledger" },
        ledgerRow(view.spanLabel, view.totalFigure ?? ""),
        view.busiest === null ? null : ledgerRow("Busiest week", view.busiest),
        ledgerRow("Weeks with no commits", String(view.quietWeeks)),
      ),
      commitChart(view),
      h("p", { class: "chart-axis" }, h("span", {}, `Week of ${view.firstWeek ?? ""}`), h("span", {}, `Week of ${view.lastWeek ?? ""}`)),
      h("p", { class: "note" }, COMMIT_ACTIVITY_NOTE),
      h("p", { class: "note" }, COMMIT_ACTIVITY_DERIVED),
      h(
        "details",
        { class: "weeks-details" },
        h("summary", {}, "Show the weekly counts as a table"),
        h(
          "table",
          { class: "weeks" },
          h("caption", { class: "sr-only" }, "Commits per week, oldest first"),
          h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Week starting"), h("th", { scope: "col", class: "num" }, "Commits"))),
          h("tbody", {}, ...view.rows.map((row) => h("tr", {}, h("th", { scope: "row" }, row.week, row.inProgress ? h("span", { class: "bot-mark" }, "in progress") : null), h("td", { class: "num" }, row.commits)))),
        ),
      ),
    );
  }
  const node = h("div", { class: "activity-result" }, band, main, evidence, view.state === "computing" ? null : h("p", { class: "head-links" }, again));
  return { node, focus: (fallback) => (view.state === "computing" ? again : fallback).focus({ preventScroll: true }) };
}

/**
 * The section that reads a year of weekly commit counts. Like the contributors section it asks nothing until the
 * button is pressed and says what that costs. GitHub computes these in the background, so the first ask may come
 * back "still computing": the page then offers to check again and never polls by itself, because each check spends
 * a request from a small quota.
 */
function commitActivitySection(analysis: RepositoryAnalysis, signal: AbortSignal, token: number): HTMLElement {
  const [owner = "", repo = ""] = analysis.full_name.split("/", 2);
  const heading = h("h2", { class: "small-heading", id: "commit-activity-title", tabindex: "-1" }, "Commit activity");
  const body = h("div", { class: "activity-body" });
  const section = h("section", { class: "commit-activity", "aria-labelledby": "commit-activity-title" }, heading, body);

  const prompt = (): void => {
    const ask = h("button", { type: "button", class: "primary", "aria-describedby": "commit-activity-cost" }, "Load a year of commit activity");
    ask.addEventListener("click", () => void load());
    clear(body);
    fill(body, h("p", { class: "note", id: "commit-activity-cost" }, COMMIT_ACTIVITY_COST), h("p", {}, ask));
    body.removeAttribute("aria-busy");
  };

  const load = async (): Promise<void> => {
    clear(body);
    fill(body, h("p", { class: "state-line", role: "status" }, "Asking GitHub for commit activity…"));
    body.setAttribute("aria-busy", "true");
    announce(`Reading commit activity of ${analysis.full_name} from GitHub`);
    try {
      const activity = await client.get("get_commit_activity", { path: { owner, repo }, signal });
      if (token !== generation) return;
      const built = commitResult(activity, () => void load());
      clear(body);
      fill(body, built.node);
      body.removeAttribute("aria-busy");
      built.focus(heading);
      section.scrollIntoView({ block: "nearest" });
      announce(describeCommitActivity(activity).summary);
      void refreshQuota(signal, token); // this read spent some of GitHub's quota, a "computing" answer included
    } catch (error) {
      if (token !== generation || (error as { code?: unknown } | null)?.code === "aborted") return;
      const failure = repositoryFailure(error, analysis.full_name);
      clear(body);
      fill(body, sectionFailure(failure, () => void load()));
      body.removeAttribute("aria-busy");
      announce(`${failure.title}. ${failure.body}`);
      void refreshQuota(signal, token);
    }
  };

  prompt();
  return section;
}


function renderReport(analysis: RepositoryAnalysis, note: string | null, signal: AbortSignal, token: number): void {
  const [owner = "", name = ""] = analysis.full_name.split("/", 2);
  const ageText = h("span", { class: "age", "aria-live": "off" });
  const cacheLabel = h("span", { class: "cache-label" });
  const band = h("section", { class: "provenance-band", "aria-labelledby": "provenance-title" });
  const paintCache = (): void => {
    const cache = describeCache(analysis.cache_status, analysis.fetched_at, Date.now());
    cacheLabel.textContent = cache.label;
    ageText.textContent = cache.text;
    band.setAttribute("data-tone", cache.tone);
  };
  paintCache();
  fill(
    band,
    h("h2", { class: "sr-only", id: "provenance-title" }, "Where this came from"),
    h("p", { class: "band-line" }, provenanceBadge(analysis.provenance), cacheLabel, ageText),
    h("p", { class: "note" }, "Every figure below is what GitHub reported at that time. VYRON adds nothing to it and stores nothing."),
    h("ul", { class: "evidence", "aria-label": "Read from" }, ...analysis.source_urls.map((url) => h("li", {}, safeLink(url, url)))),
  );

  const refresh = h("button", { type: "button", class: "text-button" }, "Check again");
  refresh.addEventListener("click", () => void analyze(analysis.full_name, "replace"));
  const exportBtn = h("button", { type: "button", class: "text-button" }, "Export JSON");
  exportBtn.addEventListener("click", () => {
    downloadJson(`vyron-repo-${owner}-${name}`, {
      exported_at: new Date().toISOString(),
      analysis,
    });
    announce(`Exported ${analysis.full_name} report as JSON.`);
  });
  const suggested = suggestedPackageForRepo(analysis);
  const depLink = suggested
    ? h(
        "a",
        { class: "text-link", href: `/dependencies.html?package=${encodeURIComponent(`${suggested.registry}:${suggested.name}`)}` },
        `Look up ${suggested.name} on ${suggested.registry === "pypi" ? "PyPI" : "npm"}`,
      )
    : null;
  const compareLink = h("a", { class: "text-link", href: `/compare.html?mode=repos&a=${encodeURIComponent(analysis.full_name)}` }, "Compare repository");

  const tags: HTMLElement[] = [h("li", { class: "tag" }, "Public")];
  if (analysis.archived) tags.push(h("li", { class: "tag is-notable" }, "Archived"));
  if (analysis.is_fork) tags.push(h("li", { class: "tag" }, "Fork"));
  tags.push(h("li", { class: "tag" }, describeLicense(analysis)));
  tags.push(h("li", { class: "tag" }, `Default branch ${analysis.default_branch}`));
  if (analysis.primary_language) tags.push(h("li", { class: "tag" }, `Mostly ${analysis.primary_language}`));

  const report = h(
    "article",
    { class: "report", "data-cache": analysis.cache_status },
    h(
      "header",
      { class: "report-head" },
      h(
        "h1",
        { class: "repo-name", id: "reading-title", tabindex: "-1" },
        h("span", { class: "owner" }, owner),
        h("span", { class: "slash", "aria-hidden": "true" }, "/"),
        h("span", { class: "sr-only" }, " slash "),
        h("span", {}, name),
      ),
      analysis.description ? h("p", { class: "repo-desc" }, analysis.description) : h("p", { class: "repo-desc dust" }, "No description."),
      h("ul", { class: "tags" }, ...tags),
      h("p", { class: "head-links" }, safeLink(analysis.html_url, "View on GitHub", "text-link"), depLink, compareLink, refresh, exportBtn),
    ),
    note ? h("p", { class: "note-line" }, note) : null,
    band,
    h(
      "div",
      { class: "report-body" },
      h(
        "section",
        { "aria-labelledby": "counts-title" },
        h("h2", { class: "small-heading", id: "counts-title" }, "Counts"),
        h(
          "dl",
          { class: "ledger" },
          ledgerRow("Stars", formatCount(analysis.stars)),
          ledgerRow("Forks", formatCount(analysis.forks)),
          ledgerRow("Open issues and pull requests", formatCount(analysis.open_issues), "GitHub counts both together."),
          ledgerRow("Created", formatDay(analysis.created_at, "Unknown")),
          ledgerRow("Last push", formatDay(analysis.pushed_at, "No pushes yet")),
        ),
      ),
      languageSection(analysis.languages),
    ),
    analysis.topics.length > 0
      ? h("section", { class: "topics", "aria-labelledby": "topics-title" }, h("h2", { class: "small-heading", id: "topics-title" }, "Topics"), h("ul", { class: "tags" }, ...analysis.topics.map((topic) => h("li", { class: "tag" }, topic))))
      : h("section", { class: "topics", "aria-labelledby": "topics-title" }, h("h2", { class: "small-heading", id: "topics-title" }, "Topics"), h("p", { class: "dust" }, "No topics set.")),
    activitySection(analysis, signal, token),
    commitActivitySection(analysis, signal, token),
  );
  show(report, true);
  // The age moves on while the page is open; the text is outside any live region so it is never re-read.
  stopAgeTimer();
  agePainters = [paintCache];
  ageTimer = window.setInterval(() => agePainters.forEach((paint) => paint()), AGE_REFRESH_MS);
  const cache = describeCache(analysis.cache_status, analysis.fetched_at, Date.now());
  announce(`${analysis.full_name}: ${formatCount(analysis.stars)} stars. ${cache.label}. ${cache.text}`);
}

// ---- quota ---------------------------------------------------------------------------------------

async function refreshQuota(signal: AbortSignal, mine: number): Promise<void> {
  let quota: RateLimitStatus;
  try {
    quota = await client.get("get_github_rate_limit", { signal });
  } catch {
    if (mine === generation) quotaLine.textContent = ""; // not essential: say nothing rather than guess
    return;
  }
  if (mine !== generation) return;
  const line = describeQuota(quota);
  quotaLine.textContent = line ? line.text : "";
  quotaLine.className = line?.low ? "warn" : "";
}

// ---- the flow ------------------------------------------------------------------------------------

type AddressMode = "push" | "replace" | "none";

function updateAddress(fullName: string | null, mode: AddressMode): void {
  if (mode === "none") return;
  const url = `${location.pathname}${writeRepoParam(fullName)}`;
  if (mode === "push" && url !== `${location.pathname}${location.search}`) history.pushState(null, "", url);
  else history.replaceState(null, "", url);
}

function setBusy(busy: boolean): void {
  submit.disabled = busy;
  reading.setAttribute("aria-busy", busy ? "true" : "false");
}

async function analyze(text: string, mode: AddressMode): Promise<void> {
  controller?.abort();
  const mine = new AbortController();
  controller = mine;
  const token = ++generation;
  stopAgeTimer();
  input.value = text;
  setBusy(true);
  let label = text.trim().slice(0, 100);
  renderLoading(label);
  try {
    const target = await client.get("github_parse", { query: { input: text.trim() }, signal: mine.signal });
    const resolved = requestFromLink(target);
    if ("problem" in resolved) {
      if (token !== generation) return;
      renderFailure({ title: "That is not a repository", body: resolved.problem, wait: null, requestId: null, retryable: false }, null, "status");
      return;
    }
    const { owner, repo, note } = resolved.request;
    label = `${owner}/${repo}`;
    if (token === generation) renderLoading(label);
    const analysis = await client.get("get_repository_analysis", { path: { owner, repo }, signal: mine.signal });
    if (token !== generation) return;
    updateAddress(analysis.full_name, mode);
    input.value = analysis.full_name;
    renderReport(analysis, note, mine.signal, token);
    void refreshQuota(mine.signal, token);
  } catch (error) {
    if (token !== generation || (error as { code?: unknown } | null)?.code === "aborted") return;
    renderFailure(repositoryFailure(error, label), () => void analyze(text, "none"));
    void refreshQuota(mine.signal, token);
  } finally {
    if (token === generation) setBusy(false);
  }
}

function fromAddress(): void {
  const wanted = readRepoParam(location.search);
  if (wanted === null) {
    controller?.abort();
    generation += 1;
    stopAgeTimer();
    input.value = "";
    setBusy(false);
    renderIdle();
    return;
  }
  void analyze(wanted, "none");
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (text === "") {
    input.focus();
    return;
  }
  void analyze(text, "push");
});
window.addEventListener("popstate", fromAddress);
fromAddress();
