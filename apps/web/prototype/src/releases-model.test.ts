import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import type { ReleaseEntry } from "../../../../packages/types/src/api.ts";
import {
  describeCrowding,
  describeDateMeaning,
  describeLoadButton,
  describeLoadLead,
  describeLoading,
  describeOmitted,
  describePosition,
  describeRelease,
  describeReleaseLeftOut,
  describeShowAll,
  describeStrip,
  describeUnreadableHistory,
  formatReleaseDay,
  formatReleaseMoment,
  PRE_RELEASE_SENTENCE,
  RELEASE_LIST_CAP,
  RELEASE_LEGEND,
  releaseLabel,
  releaseNotes,
  releaseRows,
  releasesFailure,
  summarizeReleases,
  VISIBLE_ROWS,
} from "./releases-model.ts";

// Made-up versions and dates, like the stand-in registry's: nothing here describes a real package.
const entry = (version: string, released_at: string, status: ReleaseEntry["status"] = "normal", pre_release = false): ReleaseEntry => ({
  version,
  released_at,
  pre_release,
  status,
});

describe("dates", () => {
  it("are the UTC calendar day, spelled out", () => {
    assert.equal(formatReleaseDay("2014-03-12T10:15:30.000000Z"), "12 March 2014");
    assert.equal(formatReleaseDay("2022-02-15T23:59:59Z"), "15 February 2022");
    assert.equal(formatReleaseDay("2024-12-31T23:30:00-05:00"), "1 January 2025");
  });

  it("show the time of day in UTC where the moment matters", () => {
    assert.equal(formatReleaseMoment("2014-03-12T10:15:30.000000Z"), "12 March 2014, 10:15 UTC");
    assert.equal(formatReleaseMoment("2022-02-15T05:07:00Z"), "15 February 2022, 05:07 UTC");
  });

  it("are returned as they came when they are not dates", () => {
    assert.equal(formatReleaseDay("soon"), "soon");
    assert.equal(formatReleaseMoment("soon"), "soon");
  });
});

describe("what a date means", () => {
  it("is said in the registry's terms and not mistaken for the day a release was announced", () => {
    const pypi = describeDateMeaning("first_file_upload", "pypi");
    const npm = describeDateMeaning("publish_time", "npm");
    assert.match(pypi, /earliest time PyPI recorded a file being uploaded/);
    assert.match(npm, /publish time npm recorded/);
    for (const text of [pypi, npm]) assert.match(text, /not necessarily the day its author tagged or announced it/);
  });
});

describe("one release", () => {
  it("is described with its version and its moment", () => {
    assert.equal(describeRelease(entry("1.0.0", "2020-03-01T09:30:00Z")), "Version 1.0.0, 1 March 2020, 09:30 UTC.");
  });

  it("says a pre-release is a reading of the number, not the registry's word", () => {
    const text = describeRelease(entry("1.1.0rc1", "2021-06-15T08:00:00Z", "normal", true));
    assert.ok(text.endsWith(PRE_RELEASE_SENTENCE));
    assert.match(text, /not something the registry says/);
  });

  it("says what yanked and deprecated mean", () => {
    assert.match(describeRelease(entry("1.5.1", "2022-02-15T10:00:00Z", "yanked")), /Yanked: PyPI marks every file of this version as one not to install unless it is asked for by exact version\./);
    assert.match(describeRelease(entry("2.0.0", "2014-05-20T10:00:00Z", "deprecated")), /Deprecated: the maintainers attached a deprecation notice to this version on npm\./);
  });

  it("can be a pre-release and yanked at once, and says both", () => {
    const text = describeRelease(entry("2.0.0b1", "2023-11-14T10:00:00Z", "yanked", true));
    assert.match(text, /Pre-release/);
    assert.match(text, /Yanked/);
  });

  it("has short notes for the table, in a fixed order", () => {
    assert.deepEqual(releaseNotes({ pre_release: false, status: "normal" }), []);
    assert.deepEqual(releaseNotes({ pre_release: true, status: "normal" }), ["pre-release by number"]);
    assert.deepEqual(releaseNotes({ pre_release: true, status: "yanked" }), ["pre-release by number", "yanked"]);
    assert.deepEqual(releaseNotes({ pre_release: false, status: "deprecated" }), ["deprecated"]);
  });

  it("has a position that counts from one", () => {
    assert.equal(describePosition(0, 23), "Release 1 of 23");
    assert.equal(describePosition(22, 23), "Release 23 of 23");
  });
});

describe("the names the page gives things", () => {
  it("labels a mark with its version, day and notes", () => {
    assert.equal(releaseLabel(entry("1.0.0", "2020-03-01T09:30:00Z")), "1.0.0, 1 March 2020");
    assert.equal(releaseLabel(entry("2.0.0b1", "2023-11-14T10:00:00Z", "yanked", true)), "2.0.0b1, 14 November 2023, pre-release by number, yanked");
  });

  it("labels the strip with the package, the count and the years, and says how to move", () => {
    const releases = [entry("1", "2014-03-12T00:00:00Z"), entry("2", "2025-04-17T00:00:00Z")];
    assert.equal(
      describeStrip("fixture-web", releases),
      "Release dates of fixture-web: 2 releases, from 2014 to 2025. Use the left and right arrow keys on the strip, or the two buttons, to move between releases.",
    );
    assert.match(describeStrip("x", []), /^Release dates of x: 0 releases\. Use the left/);
  });

  it("says what the button does and what it costs the registry, without promising what it cannot know", () => {
    const lead = describeLoadLead("fixture-web", "pypi");
    assert.match(lead, /^See when each version of fixture-web reached PyPI, from PyPI's own answer for the package\./);
    assert.match(lead, /If VYRON still holds that answer from the last ten minutes it is reused, and nothing more is asked of PyPI\.$/);
    assert.equal(describeLoadButton("fixture-web"), "Show release history for fixture-web");
    assert.equal(describeLoading("fixture-web"), "Reading the release dates for fixture-web…");
    assert.match(describeUnreadableHistory("fixture-alias"), /^fixture-alias is given as a link, path or alias rather than a package name/);
  });

  it("has one legend entry for each rule the strip draws by", () => {
    assert.deepEqual(
      RELEASE_LEGEND.map((item) => [item.shape, item.hollow]),
      [
        ["circle", false],
        ["circle", true],
        ["diamond", false],
      ],
    );
  });
});

describe("the summary", () => {
  it("says there is nothing to draw when no release has a date", () => {
    const summary = summarizeReleases([]);
    assert.equal(summary.count, 0);
    assert.match(summary.text, /no dated release/);
  });

  it("gives the count, the span and nothing else when every release is ordinary", () => {
    assert.equal(summarizeReleases([entry("1", "2020-01-02T00:00:00Z")]).text, "1 release, from 2 January 2020 to 2 January 2020.");
    assert.equal(summarizeReleases([entry("1", "2020-01-02T00:00:00Z"), entry("2", "2021-03-04T00:00:00Z")]).text, "2 releases, from 2 January 2020 to 4 March 2021.");
  });

  it("counts pre-releases, yanked and deprecated, with the right verb for one or several", () => {
    const releases = [
      entry("1.0.0rc1", "2020-01-01T00:00:00Z", "normal", true),
      entry("1.0.0", "2020-02-01T00:00:00Z"),
      entry("1.1.0", "2020-03-01T00:00:00Z", "yanked"),
      entry("2.0.0a1", "2021-01-01T00:00:00Z", "normal", true),
      entry("2.0.0", "2021-02-01T00:00:00Z", "deprecated"),
      entry("2.1.0", "2021-03-01T00:00:00Z", "deprecated"),
    ];
    const summary = summarizeReleases(releases);
    assert.deepEqual([summary.preReleases, summary.yanked, summary.deprecated], [2, 1, 2]);
    assert.equal(
      summary.text,
      "6 releases, from 1 January 2020 to 1 March 2021. 2 are pre-releases by version number. 1 is yanked. 2 are deprecated.",
    );
    assert.match(summarizeReleases([releases[0] as ReleaseEntry]).text, /1 is a pre-release by version number\./);
  });
});

describe("what was left out", () => {
  it("is one sentence for each kind, singular and plural", () => {
    assert.deepEqual(describeReleaseLeftOut([{ kind: "undated", count: 1 }]), ["1 version has no usable date in the registry's answer, so it is not on the strip."]);
    assert.deepEqual(describeReleaseLeftOut([{ kind: "undated", count: 3 }]), ["3 versions have no usable date in the registry's answer, so they are not on the strip."]);
    assert.deepEqual(describeReleaseLeftOut([{ kind: "removed", count: 1 }]), ["1 version has a recorded time but is no longer published on npm, so it is not listed."]);
    assert.match((describeReleaseLeftOut([{ kind: "implausible_date", count: 2 }])[0] as string), /^2 versions carry a date before 1991 or after the moment of the read, which cannot be right, so they are left out\.$/);
    assert.match((describeReleaseLeftOut([{ kind: "unusable", count: 1 }])[0] as string), /^1 entry is not a version a registry would issue, so it is left out\.$/);
  });

  it("keeps the API's order and says nothing when nothing was left out", () => {
    const words = describeReleaseLeftOut([
      { kind: "undated", count: 2 },
      { kind: "removed", count: 5 },
    ]);
    assert.equal(words.length, 2);
    assert.match(words[0] as string, /^2 versions have no usable date/);
    assert.match(words[1] as string, /^5 versions have a recorded time/);
    assert.deepEqual(describeReleaseLeftOut([]), []);
  });

  it("says how many earlier releases the list dropped, only when it dropped some", () => {
    assert.equal(describeOmitted(0), null);
    assert.equal(describeOmitted(1), `1 earlier release is not listed, because VYRON keeps the newest ${RELEASE_LIST_CAP}.`);
    assert.equal(describeOmitted(40), `40 earlier releases are not listed, because VYRON keeps the newest ${RELEASE_LIST_CAP}.`);
  });

  it("says when marks overlap, so the picture is not taken for a count", () => {
    assert.equal(describeCrowding(0), null);
    assert.match(describeCrowding(1) as string, /^1 mark overlaps another, because those releases are too close together/);
    assert.match(describeCrowding(12) as string, /^12 marks overlap another/);
    assert.match(describeCrowding(12) as string, /The table lists every release\.$/);
  });

  it("uses the same cap as the API's response schema", () => {
    const file = new URL("../../../../docs/openapi.json", import.meta.url);
    const document = JSON.parse(readFileSync(file, "utf8")) as { components: { schemas: { PackageReleases: { properties: { releases: { maxItems: number } } } } } };
    assert.equal(document.components.schemas.PackageReleases.properties.releases.maxItems, RELEASE_LIST_CAP);
  });
});

describe("the table", () => {
  const releases = Array.from({ length: 250 }, (_unused, index) => entry(`1.${index}.0`, `2020-01-01T00:00:00Z`, index === 7 ? "yanked" : "normal", index === 3));

  it("runs newest first, keeps each release's position and cuts to the visible rows", () => {
    const { rows, hidden } = releaseRows(releases, false);
    assert.equal(rows.length, VISIBLE_ROWS);
    assert.equal(hidden, 150);
    assert.deepEqual([rows[0]?.index, rows[0]?.version], [249, "1.249.0"]);
    assert.deepEqual([rows[99]?.index, rows[99]?.version], [150, "1.150.0"]);
  });

  it("shows every row when asked", () => {
    const { rows, hidden } = releaseRows(releases, true);
    assert.deepEqual([rows.length, hidden], [250, 0]);
    assert.deepEqual(rows[246]?.notes, ["pre-release by number"]);
    assert.deepEqual(rows[242]?.notes, ["yanked"]);
    assert.equal(rows[249]?.index, 0);
  });

  it("is short enough to show whole when it fits", () => {
    const { rows, hidden } = releaseRows(releases.slice(0, 5), false);
    assert.deepEqual([rows.length, hidden], [5, 0]);
    assert.deepEqual(releaseRows([], false), { rows: [], hidden: 0 });
  });

  it("offers to show the rest only when there is a rest", () => {
    assert.equal(describeShowAll(100, false), null);
    assert.equal(describeShowAll(101, false), "Show all 101 releases");
    assert.equal(describeShowAll(101, true), `Show only the newest ${VISIBLE_ROWS}`);
  });
});

describe("when the release history cannot be read", () => {
  const thrown = (code: string, message = "", extra: Record<string, unknown> = {}): unknown => ({ code, message, ...extra });

  it("says a registry with no dates sent none, and that the dependencies are not affected", () => {
    const pypi = releasesFailure(thrown("upstream_error", "PyPI sent no release dates for this package"), "pypi", "fixture-undated");
    assert.equal(pypi.title, "PyPI sent no release dates for fixture-undated");
    assert.match(pypi.body, /PyPI documents that list as deprecated/);
    assert.match(pypi.body, /dependencies on this page are not affected/);
    assert.equal(pypi.retryable, false);
    const npm = releasesFailure(thrown("upstream_error", "npm sent no release dates for this package"), "npm", "x");
    assert.equal(npm.title, "npm sent no release dates for x");
    assert.match(npm.body, /^npm's answer for this package has no publish times\./);
  });

  it("uses the words for reading a package in every other case", () => {
    assert.equal(releasesFailure(thrown("not_found"), "pypi", "nope").title, "No package called nope on PyPI");
    const limited = releasesFailure(thrown("upstream_rate_limited", "", { retryAfterSeconds: 60 }), "npm", "x");
    assert.equal(limited.title, "npm is limiting requests");
    assert.equal(limited.retryable, true);
    const broken = releasesFailure(thrown("upstream_error", "PyPI answered HTTP 500"), "pypi", "x");
    assert.equal(broken.title, "PyPI did not give a usable answer");
    assert.equal(broken.body, "PyPI answered HTTP 500.");
    assert.equal(broken.retryable, true);
    assert.equal(releasesFailure(thrown("network_error"), "pypi", "x").retryable, true);
  });

  it("does not take another upstream message for the no-dates answer", () => {
    const other = releasesFailure(thrown("upstream_error", "PyPI did not send valid JSON"), "pypi", "x");
    assert.equal(other.title, "PyPI did not give a usable answer");
  });

  it("carries the request id of the no-dates answer so it can be quoted", () => {
    const failure = releasesFailure(thrown("upstream_error", "PyPI sent no release dates for this package", { requestId: "req_abc" }), "pypi", "x");
    assert.equal(failure.requestId, "req_abc");
  });
});
