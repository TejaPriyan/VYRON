import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { readFileSync } from "node:fs";

import type { CommitActivity, CommitWeek, GitHubTarget, RepositoryActivity } from "../../../../packages/types/src/api.ts";
import type { LanguageRow } from "./repository-model.ts";
import {
  ACTIVITY_COST,
  COMMIT_ACTIVITY_COST,
  COMMIT_ACTIVITY_DERIVED,
  COMMIT_ACTIVITY_NOTE,
  COMPUTING_BODY,
  CONTRIBUTORS_NOTE,
  LATEST_RELEASE_NOTE,
  describeActivity,
  describeCommitActivity,
  describeAge,
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

const target = (over: Partial<GitHubTarget>): GitHubTarget => ({
  kind: "repository",
  owner: "fixture-org",
  repo: "example-repo",
  number: null,
  ref: null,
  html_url: "https://github.com/fixture-org/example-repo",
  ...over,
});

describe("requestFromLink", () => {
  it("analyses a repository link as it is", () => {
    assert.deepEqual(requestFromLink(target({})), { request: { owner: "fixture-org", repo: "example-repo", note: null } });
  });

  it("analyses the repository an issue, pull request, commit, release, folder or file belongs to, and says so", () => {
    const cases: Array<[Partial<GitHubTarget>, string]> = [
      [{ kind: "issue", number: 42 }, "issue #42"],
      [{ kind: "pull_request", number: 7 }, "pull request #7"],
      [{ kind: "commit", ref: "abc1234" }, "a commit"],
      [{ kind: "release", ref: "v1" }, "a release"],
      [{ kind: "tree", ref: "main" }, "a folder"],
      [{ kind: "blob", ref: "main" }, "a file"],
    ];
    for (const [over, thing] of cases) {
      const result = requestFromLink(target(over));
      assert.ok("request" in result, over.kind);
      assert.equal(result.request.note, `Analyzing fixture-org/example-repo, the repository that ${thing} belongs to.`);
    }
  });

  it("does not guess a repository for a link to an account", () => {
    const result = requestFromLink(target({ kind: "owner", repo: null }));
    assert.ok("problem" in result);
    assert.match(result.problem, /account fixture-org, not a repository/);
    assert.match(result.problem, /github\.com\/fixture-org\/name/);
  });

  it("treats a missing repo name as an account link whatever the kind says", () => {
    assert.ok("problem" in requestFromLink(target({ kind: "issue", repo: null })));
  });
});

describe("the repo query parameter", () => {
  it("reads and trims it, ignoring other parameters", () => {
    assert.equal(readRepoParam("?repo=vercel%2Fnext.js&view=x"), "vercel/next.js");
    assert.equal(readRepoParam("?repo=%20a%2Fb%20"), "a/b");
  });

  it("is null when absent or blank", () => {
    assert.equal(readRepoParam(""), null);
    assert.equal(readRepoParam("?repo="), null);
    assert.equal(readRepoParam("?repo=%20%20"), null);
    assert.equal(readRepoParam("?other=1"), null);
  });

  it("is capped so a pasted essay cannot become an address", () => {
    assert.equal(readRepoParam(`?repo=${"a".repeat(1000)}`)?.length, 300);
  });

  it("writes a query string that reads back to the same text, escaping what needs it", () => {
    for (const name of ["vercel/next.js", "a/b c", "x/y&z=1", "ü/ö"]) {
      assert.equal(readRepoParam(writeRepoParam(name)), name);
    }
    assert.equal(writeRepoParam(null), "");
    assert.equal(writeRepoParam("a/b"), "?repo=a%2Fb");
  });
});

describe("describeAge", () => {
  const now = Date.parse("2026-10-21T10:00:00Z");
  const at = (secondsAgo: number): string => new Date(now - secondsAgo * 1000).toISOString();

  it("steps through seconds, minutes, hours and days with correct plurals", () => {
    const cases: Array<[number, string]> = [
      [0, "just now"], [4, "just now"], [5, "5 seconds ago"], [59, "59 seconds ago"], [60, "1 minute ago"],
      [119, "1 minute ago"], [120, "2 minutes ago"], [3599, "59 minutes ago"], [3600, "1 hour ago"],
      [7200, "2 hours ago"], [86_399, "23 hours ago"], [86_400, "1 day ago"], [3 * 86_400, "3 days ago"],
    ]; // prettier-ignore
    for (const [seconds, expected] of cases) assert.equal(describeAge(at(seconds), now), expected, String(seconds));
  });

  it("never shows a negative age when the clocks disagree", () => {
    assert.equal(describeAge(new Date(now + 60_000).toISOString(), now), "just now");
  });

  it("says so when the timestamp cannot be read", () => {
    assert.equal(describeAge("yesterday", now), "at an unknown time");
    assert.equal(describeAge("", now), "at an unknown time");
  });
});

describe("describeLicense", () => {
  it("shows a standard identifier as GitHub gave it", () => {
    const flask = { license_spdx: "BSD-3-Clause", license_name: 'BSD 3-Clause "New" or "Revised" License' };
    assert.equal(describeLicense(flask), "License BSD-3-Clause");
  });

  it("shows GitHub's own name, and says there is no standard identifier, when there is none (torvalds/linux)", () => {
    assert.equal(describeLicense({ license_spdx: null, license_name: "Other" }), "License Other (no standard identifier)");
  });

  it("says GitHub identified none, never that the authors declared none, when GitHub reports no licence", () => {
    const text = describeLicense({ license_spdx: null, license_name: null });
    assert.equal(text, "No license identified by GitHub");
    assert.doesNotMatch(text, /declared/i);
  });

  it("prefers the identifier when both are present", () => {
    assert.equal(describeLicense({ license_spdx: "MIT", license_name: "MIT License" }), "License MIT");
  });
});

describe("describeCache", () => {
  const now = Date.parse("2026-10-21T10:00:00Z");
  const thirtyAgo = "2026-10-21T09:59:30Z";

  it("fresh: read from GitHub, with its age", () => {
    assert.deepEqual(describeCache("fresh", thirtyAgo, now), { label: "Live", text: "Read from GitHub 30 seconds ago.", tone: "ok" });
    assert.equal(describeCache("fresh", "2026-10-21T10:00:00Z", now).text, "Read from GitHub just now.");
  });

  it("revalidated: GitHub confirmed nothing changed, which is not the same as reading it again", () => {
    const note = describeCache("revalidated", thirtyAgo, now);
    assert.equal(note.label, "Confirmed");
    assert.match(note.text, /Checked with GitHub 30 seconds ago; nothing had changed/);
    assert.equal(note.tone, "ok");
  });

  it("stale: says GitHub was unreachable, shows the copy's real age and warns", () => {
    const note = describeCache("stale", "2026-10-21T09:45:00Z", now);
    assert.equal(note.label, "Stale");
    assert.match(note.text, /could not be reached/);
    assert.match(note.text, /15 minutes ago/);
    assert.match(note.text, /may be out of date/);
    assert.equal(note.tone, "warn");
  });
});

describe("figures", () => {
  it("groups counts", () => {
    assert.equal(formatCount(0), "0");
    assert.equal(formatCount(1234), "1,234");
    assert.equal(formatCount(1_234_567), "1,234,567");
  });

  it("sizes in binary units", () => {
    assert.equal(formatBytes(0), "0 B");
    assert.equal(formatBytes(1023), "1023 B");
    assert.equal(formatBytes(1024), "1.0 KiB");
    assert.equal(formatBytes(7000), "6.8 KiB");
    assert.equal(formatBytes(5 * 1024 * 1024), "5.0 MiB");
    assert.equal(formatBytes(3 * 1024 ** 3), "3.0 GiB");
    assert.equal(formatBytes(2048 * 1024 ** 4), "2048.0 TiB"); // stops at the largest unit
  });

  it("formats a day in UTC and uses the given words when there is none", () => {
    assert.equal(formatDay("2018-03-04T05:06:07Z", "Unknown"), "4 Mar 2018");
    assert.equal(formatDay("2026-10-20T23:59:59Z", "Unknown"), "20 Oct 2026");
    assert.equal(formatDay(null, "No pushes yet"), "No pushes yet");
  });
});

describe("languageBreakdown", () => {
  it("orders by bytes, ties by name, and reports shares of the total", () => {
    const result = languageBreakdown({ Shell: 1000, Python: 7000, JavaScript: 2000 });
    assert.deepEqual(result.rows.map((r) => r.name), ["Python", "JavaScript", "Shell"]);
    assert.deepEqual(result.rows.map((r) => r.percent), [70, 20, 10]);
    assert.equal(result.totalBytes, 10_000);
    assert.equal(result.other, null);
    assert.equal(result.otherCount, 0);
    const tied = languageBreakdown({ B: 5, A: 5, C: 5 });
    assert.deepEqual(tied.rows.map((r) => r.name), ["A", "B", "C"]);
  });

  it("rounds percentages to one decimal place and keeps the exact share", () => {
    const result = languageBreakdown({ A: 1, B: 2 });
    assert.deepEqual(result.rows.map((r) => r.percent), [66.7, 33.3]);
    assert.ok(Math.abs((result.rows[0]?.share ?? 0) - 2 / 3) < 1e-12);
  });

  it("never shows a language that has bytes as 0.0%", () => {
    // pallets/flask as GitHub reported it on 2026-10-26: Shell is 193 bytes and CSS 18 of 565,722.
    const result = languageBreakdown({ CSS: 18, HTML: 405, Python: 565_106, Shell: 193 });
    assert.deepEqual(result.rows.map((r) => r.name), ["Python", "HTML", "Shell", "CSS"]);
    assert.deepEqual(result.rows.map((r) => r.percent), [99.9, 0.1, 0, 0]);
    assert.deepEqual(result.rows.map(formatPercent), ["99.9%", "0.1%", "<0.1%", "<0.1%"]);
  });

  it("formats whole, ordinary and boundary shares", () => {
    const row = (share: number): LanguageRow => ({ name: "X", bytes: 1, share, percent: Math.round(share * 1000) / 10 });
    assert.equal(formatPercent(row(1)), "100.0%");
    assert.equal(formatPercent(row(0.5)), "50.0%");
    assert.equal(formatPercent(row(0.0005)), "0.1%"); // 0.05% rounds up to the first shown figure
    assert.equal(formatPercent(row(0.00049)), "<0.1%");
    assert.equal(formatPercent(row(0)), "0.0%"); // no bytes at all is not a language in the list; this is just the number
  });

  it("drops languages with no bytes and anything that is not a finite number", () => {
    const result = languageBreakdown({ Zero: 0, Negative: -5, Nope: Number.NaN, Real: 10 });
    assert.deepEqual(result.rows.map((r) => r.name), ["Real"]);
  });

  it("is empty, not NaN, when there is nothing to show", () => {
    for (const input of [{}, { Zero: 0 }]) {
      const result = languageBreakdown(input);
      assert.deepEqual(result, { rows: [], otherCount: 0, other: null, totalBytes: 0 });
    }
  });

  it("folds the smallest into Other past the row limit, keeping the limit in total", () => {
    const input: Record<string, number> = {};
    for (let i = 1; i <= 12; i += 1) input[`L${String(i).padStart(2, "0")}`] = 1000 - i;
    const result = languageBreakdown(input, 8);
    assert.equal(result.rows.length, 7);
    assert.equal(result.other?.name, "Other");
    assert.equal(result.otherCount, 5);
    assert.equal(result.rows.length + 1, 8);
    const sum = result.rows.reduce((s, r) => s + r.bytes, 0) + (result.other?.bytes ?? 0);
    assert.equal(sum, result.totalBytes);
    assert.equal(result.rows[0]?.name, "L01");
  });

  it("does not fold when the languages exactly fit", () => {
    const input: Record<string, number> = {};
    for (let i = 1; i <= 8; i += 1) input[`L${i}`] = 100 + i;
    const result = languageBreakdown(input, 8);
    assert.equal(result.rows.length, 8);
    assert.equal(result.other, null);
  });

  it("never folds everything into a lone Other, however small the limit", () => {
    for (const limit of [0, 1, 2]) {
      const result = languageBreakdown({ A: 3, B: 2, C: 1 }, limit);
      assert.equal(result.rows[0]?.name, "A", `limit ${limit}`);
      assert.equal(result.rows.length, 1, `limit ${limit}`);
      assert.equal(result.other?.name, "Other");
      assert.equal(result.otherCount, 2);
      assert.equal(result.other?.bytes, 3);
    }
  });

  it("treats a language name as inert text (it is only ever drawn as a text node)", () => {
    const result = languageBreakdown({ "<img src=x onerror=alert(1)>": 5 });
    assert.equal(result.rows[0]?.name, "<img src=x onerror=alert(1)>");
  });
});

describe("repositoryFailure", () => {
  const err = (code: string, message = "", extra: Record<string, unknown> = {}) => ({ code, message, ...extra });

  it("not_found: says the repository is missing and that private ones look the same", () => {
    const failure = repositoryFailure(err("not_found", "GitHub repository not found"), "fixture-org/missing-repo");
    assert.equal(failure.title, "No public repository called fixture-org/missing-repo");
    assert.match(failure.body, /public repositories only/);
    assert.equal(failure.retryable, false);
  });

  it("not_found because it moved: says it moved, not that it is missing", () => {
    const failure = repositoryFailure(err("not_found", "GitHub repository was renamed or moved; use its current name"), "a/b");
    assert.equal(failure.title, "a/b has moved");
    assert.doesNotMatch(failure.title, /No public/);
  });

  it("validation_error: tells the reader what a repository name looks like", () => {
    const failure = repositoryFailure(err("validation_error"), "x");
    assert.equal(failure.title, "That is not a repository name");
    assert.match(failure.body, /owner\/name/);
  });

  it("upstream_rate_limited: gives the wait and offers retry", () => {
    const failure = repositoryFailure(err("upstream_rate_limited", "x", { retryAfterSeconds: 181 }), "a/b");
    assert.equal(failure.title, "GitHub's rate limit was reached");
    assert.equal(failure.wait, "Wait 3 minutes 1 second.");
    assert.equal(failure.retryable, true);
    assert.match(failure.body, /60 to 5,000/);
  });

  it("upstream_error: uses the API's own specific message and the request id", () => {
    const failure = repositoryFailure(err("upstream_error", "GitHub denied the request", { requestId: "req-12345678" }), "a/b");
    assert.equal(failure.title, "GitHub did not give a usable answer");
    assert.equal(failure.body, "GitHub denied the request.");
    assert.equal(failure.requestId, "req-12345678");
    assert.equal(failure.retryable, true);
  });

  it("upstream_error: does not double a full stop or leave an empty body", () => {
    assert.equal(repositoryFailure(err("upstream_error", "GitHub could not be reached."), "a/b").body, "GitHub could not be reached.");
    assert.equal(repositoryFailure(err("upstream_error", ""), "a/b").body, "");
  });

  it("service_unavailable because GitHub is off: says how to switch it on", () => {
    const failure = repositoryFailure(err("service_unavailable", "GitHub access is switched off on this server"), "a/b");
    assert.equal(failure.title, "GitHub access is switched off");
    assert.match(failure.body, /--no-github/);
    assert.equal(failure.retryable, false);
  });

  it("any other code falls back to the shared copy", () => {
    const failure = repositoryFailure(err("network_error", "x"), "a/b");
    assert.equal(failure.title, "Cannot reach VYRON");
    assert.equal(failure.retryable, true);
    const unknown = repositoryFailure(new Error("boom"), "a/b");
    assert.equal(unknown.title, "Cannot reach VYRON");
    const nothing = repositoryFailure(null, "a/b");
    assert.equal(nothing.title, "Cannot reach VYRON");
  });

  it("never repeats an upstream sentence it was not given by the API's own safe messages", () => {
    const failure = repositoryFailure(err("rate_limited", "token=ghp_SECRET", { retryAfterSeconds: 3 }), "a/b");
    assert.doesNotMatch(JSON.stringify(failure), /ghp_SECRET/);
  });
});

describe("describeQuota", () => {
  it("says how many requests are left and until when", () => {
    const line = describeQuota({ limit: 60, remaining: 58, reset_at: "2026-10-21T10:53:20Z" });
    assert.deepEqual(line, { text: "58 of 60 GitHub requests left until 10:53 UTC", low: false });
  });

  it("is low at a tenth or less", () => {
    assert.equal(describeQuota({ limit: 60, remaining: 6, reset_at: null })?.low, true);
    assert.equal(describeQuota({ limit: 60, remaining: 7, reset_at: null })?.low, false);
    assert.equal(describeQuota({ limit: 5000, remaining: 0, reset_at: null })?.low, true);
  });

  it("leaves out the time when GitHub gave none and groups large numbers", () => {
    assert.equal(describeQuota({ limit: 5000, remaining: 4990, reset_at: null })?.text, "4,990 of 5,000 GitHub requests left");
  });

  it("is null when GitHub has not said", () => {
    assert.equal(describeQuota({ limit: null, remaining: null, reset_at: null }), null);
    assert.equal(describeQuota({ limit: 60, remaining: null, reset_at: null }), null);
  });
});

// The shapes below are what the API's schema allows. They were written by hand, not read from GitHub.
const activity = (over: Partial<RepositoryActivity> = {}): RepositoryActivity => ({
  full_name: "fixture-org/example-repo",
  html_url: "https://github.com/fixture-org/example-repo",
  contributors_status: "listed",
  contributors: [
    { login: "fixture-person-a", contributions: 1204, is_bot: false, profile_url: "https://github.com/fixture-person-a" },
    { login: "fixture-person-b", contributions: 1, is_bot: false, profile_url: "https://github.com/fixture-person-b" },
    { login: "fixture-helper[bot]", contributions: 30, is_bot: true, profile_url: null },
  ],
  latest_release: {
    tag_name: "v1.2.3",
    name: "Fixture release",
    published_at: "2026-09-01T12:00:00Z",
    prerelease: false,
    html_url: "https://github.com/fixture-org/example-repo/releases/tag/v1.2.3",
  },
  fetched_at: "2026-10-21T10:15:30Z",
  cache_status: "fresh",
  source_urls: ["https://api.github.com/repos/fixture-org/example-repo"],
  provenance: "sourced",
  ...over,
});

describe("describeActivity", () => {
  it("turns each contributor into a row with GitHub's count, grouped and pluralised", () => {
    const view = describeActivity(activity());
    assert.deepEqual(
      view.rows.map((r) => [r.login, r.commits]),
      [
        ["fixture-person-a", "1,204 commits"],
        ["fixture-person-b", "1 commit"],
        ["fixture-helper[bot]", "30 commits"],
      ],
    );
    assert.equal(view.noContributors, null);
  });

  it("keeps the API's profile address and the bot flag exactly, and invents no link", () => {
    const rows = describeActivity(activity()).rows;
    assert.equal(rows[0]?.profileUrl, "https://github.com/fixture-person-a");
    assert.deepEqual([rows[2]?.isBot, rows[2]?.profileUrl], [true, null]);
    assert.deepEqual(rows.map((r) => r.isBot), [false, false, true]);
  });

  it("says GitHub lists no contributors, without implying anything else", () => {
    const view = describeActivity(activity({ contributors_status: "none", contributors: [] }));
    assert.deepEqual(view.rows, []);
    assert.equal(view.noContributors, "GitHub lists no contributors for this repository.");
  });

  it("says GitHub declined, as GitHub's choice and not a failure of the page", () => {
    const view = describeActivity(activity({ contributors_status: "declined", contributors: [] }));
    assert.match(view.noContributors ?? "", /^GitHub declined to list this repository's contributors\./);
    assert.match(view.noContributors ?? "", /documents that it does this/);
    assert.doesNotMatch(view.noContributors ?? "", /error|failed|sorry/i);
  });

  it("describes the latest release by its tag, with the title only when it adds something", () => {
    const view = describeActivity(activity());
    assert.deepEqual(view.release, {
      tag: "v1.2.3",
      name: "Fixture release",
      published: "1 Sep 2026",
      prerelease: false,
      url: "https://github.com/fixture-org/example-repo/releases/tag/v1.2.3",
    });
    assert.equal(view.noRelease, null);
    const same = describeActivity(activity({ latest_release: { ...activity().latest_release!, name: "v1.2.3" } }));
    assert.equal(same.release?.name, null);
    const unnamed = describeActivity(activity({ latest_release: { ...activity().latest_release!, name: null } }));
    assert.equal(unnamed.release?.name, null);
  });

  it("says so when GitHub gave no publish date rather than showing a wrong one", () => {
    const view = describeActivity(activity({ latest_release: { ...activity().latest_release!, published_at: null } }));
    assert.equal(view.release?.published, "Publish date not given");
  });

  it("carries a pre-release flag through if GitHub ever sends one", () => {
    const view = describeActivity(activity({ latest_release: { ...activity().latest_release!, prerelease: true } }));
    assert.equal(view.release?.prerelease, true);
  });

  it("says there is no published release, and why a tag alone does not count", () => {
    const view = describeActivity(activity({ latest_release: null }));
    assert.equal(view.release, null);
    assert.match(view.noRelease ?? "", /^GitHub reports no published release for this repository\./);
    assert.match(view.noRelease ?? "", /a tag on its own is not a release/);
  });

  it("gives a screen reader one line that names the repository, the top account and the release", () => {
    assert.equal(
      describeActivity(activity()).summary,
      "fixture-org/example-repo: 3 contributors listed, most commits fixture-person-a; latest release v1.2.3.",
    );
    assert.equal(
      describeActivity(activity({ contributors: [activity().contributors[0]!], latest_release: null })).summary,
      "fixture-org/example-repo: 1 contributor listed, most commits fixture-person-a; no published release.",
    );
    assert.equal(
      describeActivity(activity({ contributors_status: "none", contributors: [], latest_release: null })).summary,
      "fixture-org/example-repo: no contributors listed; no published release.",
    );
  });

  it("states GitHub's meaning of 'latest' and what a commit count does not say", () => {
    assert.match(LATEST_RELEASE_NOTE, /not necessarily the highest version number/);
    assert.match(CONTRIBUTORS_NOTE, /says nothing about the quality or importance/);
  });

  it("tells the reader what asking costs before they ask", () => {
    assert.match(ACTIVITY_COST, /twice more, or three times/);
    assert.match(ACTIVITY_COST, /only happens when you ask/);
  });

  it("describes the freshness of this read with the same words as the repository's", () => {
    const now = Date.parse("2026-10-21T10:16:30Z");
    assert.equal(describeCache("fresh", "2026-10-21T10:15:30Z", now).text, "Read from GitHub 1 minute ago.");
    assert.equal(describeCache("stale", "2026-10-21T10:15:30Z", now).tone, "warn");
  });
});


const WEEK_SECONDS = 604_800;
const FIRST_WEEK = 1_762_646_400; // Sunday 2025-11-09 00:00 UTC

function weekOf(index: number, days: ReadonlyArray<number>): CommitWeek {
  return {
    week_start: new Date((FIRST_WEEK + index * WEEK_SECONDS) * 1000).toISOString().replace(".000Z", "Z"),
    days,
    total: days.reduce((all, n) => all + n, 0),
  };
}

const commitActivity = (over: Partial<CommitActivity> = {}): CommitActivity => ({
  full_name: "fixture-org/example-repo",
  html_url: "https://github.com/fixture-org/example-repo",
  status: "ready",
  weeks: [weekOf(0, [0, 3, 2, 0, 0, 0, 0]), weekOf(1, [0, 0, 0, 0, 0, 0, 0]), weekOf(2, [1, 9, 8, 7, 5, 0, 0]), weekOf(3, [0, 1, 0, 0, 0, 0, 0])],
  fetched_at: "2025-12-02T10:00:00Z", // a Tuesday in the fourth week, which began on 2025-11-30
  cache_status: "fresh",
  source_urls: ["https://api.github.com/repos/fixture-org/example-repo", "https://api.github.com/repos/fixture-org/example-repo/stats/commit_activity"],
  provenance: "sourced",
  ...over,
});

describe("describeCommitActivity", () => {
  it("keeps GitHub's weekly counts and scales the bars to the busiest week", () => {
    const view = describeCommitActivity(commitActivity());
    assert.deepEqual(view.bars.map((bar) => bar.total), [5, 0, 30, 1]);
    assert.deepEqual(view.bars.map((bar) => bar.height), [5 / 30, 0, 1, 0.03333333333333333]);
  });

  it("never draws a week that had commits as nothing", () => {
    const weeks = [weekOf(0, [1, 0, 0, 0, 0, 0, 0]), weekOf(1, [0, 0, 0, 0, 0, 0, 0]), weekOf(2, [9999, 0, 0, 0, 0, 0, 0])];
    const bars = describeCommitActivity(commitActivity({ weeks })).bars;
    assert.equal(bars[0]?.height, 0.03); // 1 of 9,999 would be invisible; it keeps a visible minimum
    assert.equal(bars[1]?.height, 0); // and a week of zero stays zero
  });

  it("adds the weeks up and names the busiest one, saying the sums are VYRON's", () => {
    const view = describeCommitActivity(commitActivity());
    assert.equal(view.totalLine, "36 commits in 4 weeks");
    assert.equal(view.totalFigure, "36");
    assert.equal(view.spanLabel, "Commits in 4 weeks");
    assert.equal(view.busiest, "30 commits, week of 23 Nov 2025");
    assert.equal(view.quietWeeks, 1);
    assert.equal(view.firstWeek, "9 Nov 2025");
    assert.equal(view.lastWeek, "30 Nov 2025");
    assert.match(COMMIT_ACTIVITY_DERIVED, /added up by VYRON/);
    assert.match(COMMIT_ACTIVITY_DERIVED, /weekly counts are GitHub's/);
  });

  it("marks the week that had not ended when GitHub was asked, from that time and not from the reader's clock", () => {
    const view = describeCommitActivity(commitActivity());
    assert.deepEqual(view.bars.map((bar) => bar.inProgress), [false, false, false, true]);
    assert.equal(view.bars[3]?.label, "Week of 30 Nov 2025: 1 commit so far");
    assert.equal(view.bars[2]?.label, "Week of 23 Nov 2025: 30 commits");
    assert.equal(view.rows[3]?.inProgress, true);
    const lastSecond = describeCommitActivity(commitActivity({ fetched_at: "2025-12-06T23:59:59Z" }));
    assert.equal(lastSecond.bars[3]?.inProgress, true); // the last second of that Saturday
    const nextWeek = describeCommitActivity(commitActivity({ fetched_at: "2025-12-07T00:00:00Z" }));
    assert.equal(nextWeek.bars[3]?.inProgress, false); // asked after the week was over: Sunday 7 Dec starts the next
  });

  it("says 'still computing' with no figures, and never a chart, when GitHub has not finished", () => {
    const view = describeCommitActivity(commitActivity({ status: "computing", weeks: [] }));
    assert.equal(view.state, "computing");
    assert.deepEqual([view.bars.length, view.totalLine, view.busiest], [0, null, null]);
    assert.match(view.summary, /still computing commit activity\. Check again in a few seconds\./);
    assert.match(COMPUTING_BODY, /VYRON never checks on its own/);
  });

  it("names no busiest week, and draws no bars, when no week had a commit", () => {
    const weeks = [weekOf(0, [0, 0, 0, 0, 0, 0, 0]), weekOf(1, [0, 0, 0, 0, 0, 0, 0])];
    const view = describeCommitActivity(commitActivity({ weeks }));
    assert.equal(view.busiest, null);
    assert.equal(view.totalLine, "0 commits in 2 weeks");
    assert.equal(view.quietWeeks, 2);
    assert.deepEqual(view.bars.map((bar) => bar.height), [0, 0]);
    assert.equal(view.summary, "fixture-org/example-repo: 0 commits in 2 weeks. 2 weeks with no commits.");
  });

  it("says GitHub reported nothing when it did", () => {
    const view = describeCommitActivity(commitActivity({ status: "none", weeks: [] }));
    assert.equal(view.state, "none");
    assert.equal(view.summary, "fixture-org/example-repo: GitHub reports no commit activity.");
  });

  it("writes one line a screen reader can use", () => {
    assert.equal(
      describeCommitActivity(commitActivity()).summary,
      "fixture-org/example-repo: 36 commits in 4 weeks. Busiest week 30 commits, week of 23 Nov 2025. 1 week with no commits.",
    );
  });

  it("gives the table the same weeks, oldest first, in the same words", () => {
    const rows = describeCommitActivity(commitActivity()).rows;
    assert.deepEqual(rows.map((row) => [row.week, row.commits]), [["9 Nov 2025", "5"], ["16 Nov 2025", "0"], ["23 Nov 2025", "30"], ["30 Nov 2025", "1"]]);
  });

  it("tells the reader what asking costs, and that the first ask may not be answered", () => {
    assert.match(COMMIT_ACTIVITY_COST, /once more, or twice/);
    assert.match(COMMIT_ACTIVITY_COST, /still computing/);
    assert.match(COMMIT_ACTIVITY_COST, /only happens when you ask/);
    assert.match(COMMIT_ACTIVITY_NOTE, /says nothing about the quality or importance/);
    assert.match(COMMIT_ACTIVITY_NOTE, /last bar is the current week/);
  });

  it("reads the real answer GitHub gave for torvalds/linux on 2026-11-04", () => {
    const file = new URL("../../../api/tests/fixtures/github_real/commit_activity_flask_linux/repos__torvalds__linux__stats__commit_activity__attempt-1.json", import.meta.url);
    const capture = JSON.parse(readFileSync(file, "utf8")) as { body: Array<{ days: number[]; total: number; week: number }> };
    const weeks = capture.body.map((w) => weekOf((w.week - FIRST_WEEK) / WEEK_SECONDS, w.days));
    const view = describeCommitActivity(commitActivity({ weeks, fetched_at: "2026-11-04T10:01:04Z" }));
    assert.equal(view.bars.length, 52);
    assert.equal(view.totalLine, "76,597 commits in 52 weeks");
    assert.equal(view.firstWeek, "9 Nov 2025");
    assert.equal(view.lastWeek, "1 Nov 2026");
    assert.equal(view.bars[51]?.inProgress, true); // captured on the Wednesday of that week
    assert.equal(view.bars.filter((bar) => bar.inProgress).length, 1);
    assert.equal(Math.max(...view.bars.map((bar) => bar.height)), 1);
  });
});

describe("suggestedPackageForRepo", () => {
  it("suggests PyPI for Python and npm for JavaScript or TypeScript repositories", () => {
    assert.deepEqual(suggestedPackageForRepo({ full_name: "pallets/flask", primary_language: "Python" }), {
      registry: "pypi",
      name: "flask",
    });
    assert.deepEqual(suggestedPackageForRepo({ full_name: "expressjs/express", primary_language: "JavaScript" }), {
      registry: "npm",
      name: "express",
    });
    assert.deepEqual(suggestedPackageForRepo({ full_name: "microsoft/TypeScript", primary_language: "TypeScript" }), {
      registry: "npm",
      name: "typescript",
    });
  });

  it("returns null for other languages or missing names", () => {
    assert.equal(suggestedPackageForRepo({ full_name: "torvalds/linux", primary_language: "C" }), null);
    assert.equal(suggestedPackageForRepo({ full_name: "owner/repo", primary_language: null }), null);
  });
});
