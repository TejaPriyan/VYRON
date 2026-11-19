import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { PackageReleases, PackageReport, RepositoryAnalysis } from "../../../../packages/types/src/api.ts";
import {
  comparePackageRows,
  compareRepoRows,
  diffDependencies,
  diffLanguages,
  readCompareParams,
  writeCompareParams,
} from "./compare-model.ts";

const pkg = (over: Partial<PackageReport> = {}): PackageReport => ({
  registry: "pypi",
  name: "flask",
  version: "3.1.3",
  summary: "A web framework.",
  license: "BSD-3-Clause",
  homepage: "https://github.com/pallets/flask",
  registry_url: "https://pypi.org/project/flask/",
  dependencies: [
    { name: "werkzeug", requirement: ">=3.1", readable: true },
    { name: "jinja2", requirement: ">=3.1.2", readable: true },
  ],
  left_out: [{ kind: "conditional", count: 3 }],
  fetched_at: "2026-11-19T05:00:00Z",
  cache_status: "fresh",
  source_url: "https://pypi.org/pypi/flask/json",
  provenance: "sourced",
  ...over,
});

const repo = (over: Partial<RepositoryAnalysis> = {}): RepositoryAnalysis => ({
  full_name: "pallets/flask",
  html_url: "https://github.com/pallets/flask",
  description: "The Python micro framework.",
  default_branch: "main",
  primary_language: "Python",
  languages: { Python: 900, HTML: 100 },
  topics: ["python", "web"],
  license_spdx: "BSD-3-Clause",
  license_name: "BSD 3-Clause",
  stars: 70000,
  forks: 16000,
  open_issues: 5,
  archived: false,
  is_fork: false,
  created_at: "2010-04-06T11:11:59Z",
  pushed_at: "2026-11-01T12:00:00Z",
  fetched_at: "2026-11-19T05:00:00Z",
  cache_status: "fresh",
  source_urls: ["https://api.github.com/repos/pallets/flask"],
  provenance: "sourced",
  ...over,
});

describe("compare URL parameters", () => {
  it("defaults to packages mode with empty inputs", () => {
    assert.deepEqual(readCompareParams(""), { mode: "packages", a: null, b: null });
    assert.equal(writeCompareParams({ mode: "packages", a: null, b: null }), "");
  });

  it("round-trips mode, a, and b cleanly", () => {
    const query = writeCompareParams({ mode: "repos", a: "pallets/flask", b: "django/django" });
    assert.deepEqual(readCompareParams(query), { mode: "repos", a: "pallets/flask", b: "django/django" });
  });

  it("falls back to packages mode for unknown mode values", () => {
    assert.equal(readCompareParams("?mode=invalid&a=pypi:flask").mode, "packages");
  });
});

describe("diffDependencies", () => {
  it("separates shared, only-A, and only-B direct dependencies case-insensitively", () => {
    const left = pkg({
      dependencies: [
        { name: "Werkzeug", requirement: ">=3.1", readable: true },
        { name: "click", requirement: ">=8.1", readable: true },
      ],
    });
    const right = pkg({
      name: "quart",
      dependencies: [
        { name: "werkzeug", requirement: ">=3.0", readable: true },
        { name: "hypercorn", requirement: ">=0.16", readable: true },
      ],
    });
    const diff = diffDependencies(left, right);
    assert.deepEqual(diff.shared, [{ name: "Werkzeug", reqA: ">=3.1", reqB: ">=3.0" }]);
    assert.deepEqual(diff.onlyA, [{ name: "click", req: ">=8.1" }]);
    assert.deepEqual(diff.onlyB, [{ name: "hypercorn", req: ">=0.16" }]);
  });
});

describe("comparePackageRows", () => {
  it("builds structured comparison rows including release history counts and spans", () => {
    const historyA: PackageReleases = {
      registry: "pypi",
      name: "flask",
      registry_url: "https://pypi.org/project/flask/",
      releases: [
        { version: "0.1", released_at: "2010-04-16T14:29:37Z", pre_release: false, status: "normal" },
        { version: "3.1.3", released_at: "2026-02-19T05:00:56Z", pre_release: false, status: "normal" },
      ],
      omitted_older: 0,
      left_out: [],
      date_meaning: "first_file_upload",
      fetched_at: "2026-11-19T05:00:00Z",
      cache_status: "fresh",
      source_url: "https://pypi.org/pypi/flask/json",
      provenance: "sourced",
    };
    const rows = comparePackageRows(pkg(), pkg({ name: "django", version: "6.1.3", left_out: [] }), historyA, null);
    assert.equal(rows.find((r) => r.label === "Current / latest version")?.b, "6.1.3");
    assert.equal(rows.find((r) => r.label === "Release history")?.a, "2 (2010-04-16 to 2026-02-19)");
    assert.equal(rows.find((r) => r.label === "Release history")?.b, "Unavailable");
  });
});

describe("compareRepoRows and diffLanguages", () => {
  it("compares counts, status flags, and shared vs unique languages", () => {
    const a = repo();
    const b = repo({
      full_name: "django/django",
      stars: 85000,
      archived: true,
      languages: { Python: 800, JavaScript: 200 },
    });
    const rows = compareRepoRows(a, b);
    assert.equal(rows.find((r) => r.label === "Stars")?.a, "70,000");
    assert.equal(rows.find((r) => r.label === "Status")?.b, "Public · Archived");

    const langs = diffLanguages(a, b);
    assert.deepEqual(langs.shared, [{ name: "Python", pctA: "90.0%", pctB: "80.0%" }]);
    assert.deepEqual(langs.onlyA, ["HTML (10.0%)"]);
    assert.deepEqual(langs.onlyB, ["JavaScript (20.0%)"]);
  });
});
