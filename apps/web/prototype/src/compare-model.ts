/**
 * Pure comparison logic for the Compare page (no DOM, no network). Turns two live PackageReport or
 * RepositoryAnalysis answers into deterministic rows and set differences. Nothing here ranks or scores
 * either item: every figure is what GitHub, PyPI or npm reported.
 */
import type { PackageReleases, PackageReport, RepositoryAnalysis } from "../../../../packages/types/src/api.ts";
import { describeLeftOut, describeRequirement, REGISTRY_NAMES } from "./dependencies-model.ts";
import { describeLicense, formatCount, formatDay, languageBreakdown } from "./repository-model.ts";

export type CompareMode = "packages" | "repos";

export interface CompareParams {
  readonly mode: CompareMode;
  readonly a: string | null;
  readonly b: string | null;
}

const MAX_PARAM = 300;

export function readCompareParams(search: string): CompareParams {
  const params = new URLSearchParams(search);
  const rawMode = params.get("mode");
  const mode: CompareMode = rawMode === "repos" ? "repos" : "packages";
  const clean = (key: string): string | null => {
    const value = params.get(key)?.trim() ?? "";
    return value === "" ? null : value.slice(0, MAX_PARAM);
  };
  return { mode, a: clean("a"), b: clean("b") };
}

export function writeCompareParams(params: CompareParams): string {
  if (params.a === null && params.b === null && params.mode === "packages") return "";
  const query = new URLSearchParams();
  query.set("mode", params.mode);
  if (params.a) query.set("a", params.a);
  if (params.b) query.set("b", params.b);
  return `?${query.toString()}`;
}

export interface CompareRow {
  readonly label: string;
  readonly a: string;
  readonly b: string;
}

export interface SharedDependency {
  readonly name: string;
  readonly reqA: string;
  readonly reqB: string;
}

export interface UniqueDependency {
  readonly name: string;
  readonly req: string;
}

export interface DependencyDiff {
  readonly shared: ReadonlyArray<SharedDependency>;
  readonly onlyA: ReadonlyArray<UniqueDependency>;
  readonly onlyB: ReadonlyArray<UniqueDependency>;
}

/** Deterministic set difference of declared direct dependencies between two packages. */
export function diffDependencies(
  left: Pick<PackageReport, "dependencies">,
  right: Pick<PackageReport, "dependencies">,
): DependencyDiff {
  const mapA = new Map<string, string>();
  for (const dep of left.dependencies) {
    mapA.set(dep.name.toLowerCase(), describeRequirement(dep.requirement, dep.readable));
  }
  const mapB = new Map<string, string>();
  for (const dep of right.dependencies) {
    mapB.set(dep.name.toLowerCase(), describeRequirement(dep.requirement, dep.readable));
  }

  const displayNames = new Map<string, string>();
  for (const dep of left.dependencies) displayNames.set(dep.name.toLowerCase(), dep.name);
  for (const dep of right.dependencies) {
    const key = dep.name.toLowerCase();
    if (!displayNames.has(key)) displayNames.set(key, dep.name);
  }

  const shared: SharedDependency[] = [];
  const onlyA: UniqueDependency[] = [];
  const onlyB: UniqueDependency[] = [];

  for (const key of [...displayNames.keys()].sort()) {
    const name = displayNames.get(key) ?? key;
    const reqA = mapA.get(key);
    const reqB = mapB.get(key);
    if (reqA !== undefined && reqB !== undefined) shared.push({ name, reqA, reqB });
    else if (reqA !== undefined) onlyA.push({ name, req: reqA });
    else if (reqB !== undefined) onlyB.push({ name, req: reqB });
  }
  return { shared, onlyA, onlyB };
}

function releaseSummaryCell(history: PackageReleases | null): string {
  if (!history) return "Unavailable";
  const count = history.releases.length;
  if (count === 0) return "0 dated releases";
  const first = history.releases[0]?.released_at.slice(0, 10) ?? "";
  const last = history.releases[count - 1]?.released_at.slice(0, 10) ?? "";
  const suffix = history.omitted_older > 0 ? ` (+${formatCount(history.omitted_older)} older omitted)` : "";
  return `${formatCount(count)} (${first} to ${last})${suffix}`;
}

export function comparePackageRows(
  left: PackageReport,
  right: PackageReport,
  relA: PackageReleases | null,
  relB: PackageReleases | null,
): ReadonlyArray<CompareRow> {
  const leftOutText = (report: PackageReport): string => {
    const lines = describeLeftOut(report.left_out);
    return lines.length === 0 ? "None" : lines.join(" ");
  };
  return [
    { label: "Registry", a: REGISTRY_NAMES[left.registry], b: REGISTRY_NAMES[right.registry] },
    { label: "Current / latest version", a: left.version, b: right.version },
    { label: "License", a: left.license ?? "Not stated", b: right.license ?? "Not stated" },
    { label: "Direct dependencies listed", a: formatCount(left.dependencies.length), b: formatCount(right.dependencies.length) },
    { label: "Declared but left out", a: leftOutText(left), b: leftOutText(right) },
    { label: "Release history", a: releaseSummaryCell(relA), b: releaseSummaryCell(relB) },
    { label: "Provenance", a: left.provenance, b: right.provenance },
  ];
}

export function compareRepoRows(left: RepositoryAnalysis, right: RepositoryAnalysis): ReadonlyArray<CompareRow> {
  const flags = (repo: RepositoryAnalysis): string => {
    const items = ["Public"];
    if (repo.archived) items.push("Archived");
    if (repo.is_fork) items.push("Fork");
    return items.join(" · ");
  };
  return [
    { label: "Status", a: flags(left), b: flags(right) },
    { label: "License", a: describeLicense(left), b: describeLicense(right) },
    { label: "Primary language", a: left.primary_language ?? "None reported", b: right.primary_language ?? "None reported" },
    { label: "Default branch", a: left.default_branch, b: right.default_branch },
    { label: "Stars", a: formatCount(left.stars), b: formatCount(right.stars) },
    { label: "Forks", a: formatCount(left.forks), b: formatCount(right.forks) },
    { label: "Open issues & pull requests", a: formatCount(left.open_issues), b: formatCount(right.open_issues) },
    { label: "Created", a: formatDay(left.created_at, "Unknown"), b: formatDay(right.created_at, "Unknown") },
    { label: "Last push", a: formatDay(left.pushed_at, "No pushes yet"), b: formatDay(right.pushed_at, "No pushes yet") },
    { label: "Provenance", a: left.provenance, b: right.provenance },
  ];
}

export interface LanguageDiff {
  readonly shared: ReadonlyArray<{ readonly name: string; readonly pctA: string; readonly pctB: string }>;
  readonly onlyA: ReadonlyArray<string>;
  readonly onlyB: ReadonlyArray<string>;
}

export function diffLanguages(
  left: Pick<RepositoryAnalysis, "languages">,
  right: Pick<RepositoryAnalysis, "languages">,
): LanguageDiff {
  const aBreak = languageBreakdown(left.languages, 50);
  const bBreak = languageBreakdown(right.languages, 50);
  const mapA = new Map(aBreak.rows.map((r) => [r.name, r]));
  const mapB = new Map(bBreak.rows.map((r) => [r.name, r]));
  const names = [...new Set([...mapA.keys(), ...mapB.keys()])].sort();

  const shared: Array<{ name: string; pctA: string; pctB: string }> = [];
  const onlyA: string[] = [];
  const onlyB: string[] = [];
  for (const name of names) {
    const rowA = mapA.get(name);
    const rowB = mapB.get(name);
    if (rowA && rowB) {
      shared.push({ name, pctA: `${rowA.percent.toFixed(1)}%`, pctB: `${rowB.percent.toFixed(1)}%` });
    } else if (rowA) {
      onlyA.push(`${name} (${rowA.percent.toFixed(1)}%)`);
    } else if (rowB) {
      onlyB.push(`${name} (${rowB.percent.toFixed(1)}%)`);
    }
  }
  return { shared, onlyA, onlyB };
}
