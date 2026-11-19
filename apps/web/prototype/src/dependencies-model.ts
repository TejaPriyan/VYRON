/**
 * The dependencies page's logic with no DOM in it, so every sentence the page shows and every number it derives can be
 * tested. Nothing here fetches anything and nothing is invented: it turns what the API returned (`PackageReport`, an
 * error) and the graph built from it into words, and turns what a person typed or pasted into a package to ask for.
 */
import type { DependencyGraph, DepNode, NodeStatus, PackageRegistry } from "../../../../packages/graph/src/dependency-graph.ts";
import { nodeStatus } from "../../../../packages/graph/src/dependency-graph.ts";
import type { PackageLeftOut, PackageReport } from "../../../../packages/types/src/api.ts";
import { describeWait, errorState } from "../../../../packages/ui/src/states.ts";
import type { ErrorCodeKey } from "../../../../packages/ui/src/states.ts";
import { plural } from "./dom.ts";
import { describeAge } from "./repository-model.ts";

export const REGISTRY_NAMES: Readonly<Record<PackageRegistry, string>> = { pypi: "PyPI", npm: "npm" };

/** What the registries allow: npm names are at most 214 characters, and the API takes no more than that. */
export const MAX_NAME_LENGTH = 214;

// ---- what to read ------------------------------------------------------------------------------

export interface PackageRequest {
  readonly registry: PackageRegistry;
  readonly name: string;
}

export type RequestFromInput = { readonly request: PackageRequest } | { readonly problem: string };

// A name has no space, control character or quote: the API checks the real rules, this only stops the obviously wrong.
const NOT_A_NAME = /[\s\u0000-\u001f\u007f"'<>\\]/u;

/** `pypi.org/project/<name>/` and `npmjs.com/package/<name>`, as pasted from the address bar. Anything else is null. */
function nameFromLink(text: string): PackageRequest | null {
  let url: URL;
  try {
    url = new URL(/^(www\.)?(pypi\.org|npmjs\.com)\//i.test(text) ? `https://${text}` : text);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  const host = url.hostname.toLowerCase();
  const parts = url.pathname.split("/").filter((part) => part !== "");
  if (host === "pypi.org" && parts[0] === "project" && parts[1]) return { registry: "pypi", name: decodeURIComponent(parts[1]) };
  if ((host === "www.npmjs.com" || host === "npmjs.com") && parts[0] === "package" && parts[1]) {
    const scoped = parts[1].startsWith("@") && parts[2];
    return { registry: "npm", name: decodeURIComponent(scoped ? `${parts[1]}/${parts[2]}` : parts[1]) };
  }
  return null;
}

/**
 * The package to ask for, from the registry picked in the form and the text typed. A leading `pypi:` or `npm:` and a
 * pasted PyPI or npm package link both choose their own registry, so the picker is only the default.
 */
export function requestFromInput(registry: PackageRegistry, text: string): RequestFromInput {
  const trimmed = text.trim();
  if (trimmed === "") return { problem: "Type a package name, like flask or express." };
  const fromLink = nameFromLink(trimmed);
  let chosen = fromLink?.registry ?? registry;
  let name = fromLink?.name ?? trimmed;
  const prefixed = /^(pypi|npm):(.*)$/i.exec(name);
  if (prefixed) {
    chosen = (prefixed[1] as string).toLowerCase() === "npm" ? "npm" : "pypi";
    name = (prefixed[2] as string).trim();
  }
  if (fromLink === null && /^[a-z][a-z0-9+.-]*:\/\//i.test(name)) {
    return { problem: "That link is not to a PyPI or npm package page. Paste just the name, or a link like pypi.org/project/flask." };
  }
  if (name === "") return { problem: `Type a package name after ${REGISTRY_NAMES[chosen]}:.` };
  if (name.length > MAX_NAME_LENGTH) return { problem: `That name is too long. ${REGISTRY_NAMES[chosen]} names have at most ${MAX_NAME_LENGTH} characters.` };
  if (NOT_A_NAME.test(name)) return { problem: "Package names have no spaces or quotes. Paste just the name, or a link to the package's page." };
  return { request: { registry: chosen, name } };
}

/** `?package=pypi:flask` -> the package to read, or null. Anything else in the query string is ignored. */
export function readPackageParam(search: string): PackageRequest | null {
  const value = new URLSearchParams(search).get("package");
  if (value === null) return null;
  const match = /^(pypi|npm):(.+)$/.exec(value.trim());
  if (!match) return null;
  const name = (match[2] as string).trim();
  if (name === "" || name.length > MAX_NAME_LENGTH || NOT_A_NAME.test(name)) return null;
  return { registry: match[1] as PackageRegistry, name };
}

export function writePackageParam(request: PackageRequest | null): string {
  if (request === null) return "";
  const params = new URLSearchParams();
  params.set("package", `${request.registry}:${request.name}`);
  return `?${params.toString()}`;
}

/** "flask on PyPI". */
export function packageLabel(registry: PackageRegistry, name: string): string {
  return `${name} on ${REGISTRY_NAMES[registry]}`;
}

// ---- how fresh ---------------------------------------------------------------------------------

export interface CacheNote {
  readonly label: string;
  readonly text: string;
  readonly tone: "ok" | "warn";
}

/**
 * How current a package read is. `fetched_at` is when the registry last answered, so a copy VYRON kept for a few
 * minutes shows its real age, and a "stale" one says the registry could not be reached.
 */
export function describePackageCache(report: Pick<PackageReport, "cache_status" | "fetched_at" | "registry">, nowMs: number): CacheNote {
  const age = describeAge(report.fetched_at, nowMs);
  const registry = REGISTRY_NAMES[report.registry];
  if (report.cache_status === "stale") {
    return { label: "Stale", text: `${registry} could not be reached, so this is from ${age} and may be out of date.`, tone: "warn" };
  }
  return { label: "Live", text: `Read from ${registry} ${age}.`, tone: "ok" };
}

/** Said when the answer did not come from PyPI or npm: the API gives no registry page for a stand-in. */
export const STAND_IN_NOTE =
  "This answer came from a local stand-in registry started for testing, not from PyPI or npm. The packages in it are made up.";

export const isStandIn = (report: Pick<PackageReport, "registry_url">): boolean => report.registry_url === null;

// ---- the words for what was left out ---------------------------------------------------------------------

const LEFT_OUT_WORDS: Readonly<Record<PackageLeftOut["kind"], (count: number) => string>> = {
  conditional: (n) => `${plural(n, "requirement", "requirements")} that apply only with an extra or on certain systems`,
  development: (n) => `${plural(n, "development dependency", "development dependencies")}, which only people working on the package need`,
  peer: (n) => `${plural(n, "peer dependency", "peer dependencies")}, which the package expects whoever installs it to provide`,
  optional: (n) => `${plural(n, "optional dependency", "optional dependencies")}, installed only when they can be`,
  unusable: (n) => `${plural(n, "entry", "entries")} that ${n === 1 ? "is" : "are"} not ${n === 1 ? "a name" : "names"} a registry could have issued`,
};

/** One sentence per kind the package declared but the graph does not draw, in the API's order. Empty when nothing was left out. */
export function describeLeftOut(leftOut: ReadonlyArray<PackageLeftOut>): ReadonlyArray<string> {
  return leftOut.map((item) => `${LEFT_OUT_WORDS[item.kind](item.count)}.`);
}

/** The words for a requirement beside a dependency. */
export function describeRequirement(requirement: string | null, readable: boolean): string {
  if (requirement !== null && requirement !== "") return requirement;
  return readable ? "any version" : "given as a link or path";
}

// ---- the graph in words ----------------------------------------------------------------------------

export const STATUS_WORDS: Readonly<Record<NodeStatus, string>> = {
  read: "Dependencies read",
  unread: "Not opened yet",
  unreadable: "Cannot be opened",
};

export function statusHelp(status: NodeStatus, registry: PackageRegistry): string {
  switch (status) {
    case "read":
      return "Its own dependencies have been read, so every line out of it is drawn.";
    case "unread":
      return "Open it to read its own dependencies. Until then it has no lines out, which does not mean it has no dependencies.";
    case "unreadable":
      return `Another package lists this as a link or path, not a name ${REGISTRY_NAMES[registry]} can look up, so VYRON cannot open it.`;
  }
}

export interface GraphSummary {
  readonly packages: number;
  readonly dependencies: number;
  readonly unopened: number;
  readonly text: string;
}

export function describeGraph(graph: DependencyGraph): GraphSummary {
  const nodes = [...graph.nodes.values()];
  const unopened = nodes.filter((node) => nodeStatus(node) === "unread").length;
  const packages = nodes.length;
  const dependencies = graph.edges.length;
  const parts = [`${plural(packages, "package", "packages")}`, `${plural(dependencies, "dependency link", "dependency links")}`];
  const tail = unopened > 0 ? `${unopened} not opened yet` : "every package that can be opened has been";
  return { packages, dependencies, unopened, text: `${parts.join(" and ")}, ${tail}.` };
}

/** What a screen reader is told when a package has been read and added. */
export function describeRead(report: Pick<PackageReport, "name" | "registry" | "dependencies">, added: number, notDrawn: number): string {
  const listed = report.dependencies.length;
  const base =
    listed === 0
      ? `${report.name} lists no dependencies.`
      : `${report.name} lists ${plural(listed, "dependency", "dependencies")}; ${added === 0 ? "none are new to the graph" : `${added} ${added === 1 ? "is" : "are"} new to the graph`}.`;
  return notDrawn > 0 ? `${base} ${describeNotDrawn(report.name, notDrawn)}` : base;
}

export function describeNotDrawn(name: string, notDrawn: number): string {
  return `${plural(notDrawn, "dependency", "dependencies")} of ${name} ${notDrawn === 1 ? "was" : "were"} not drawn, because the graph is full.`;
}

/** A node's description for assistive technology: name, what it is, what it is waiting on. */
export function describeNode(node: DepNode, dependsOn: number, dependedOnBy: number): string {
  const status = STATUS_WORDS[nodeStatus(node)];
  const links = [dependsOn > 0 ? `depends on ${dependsOn}` : null, dependedOnBy > 0 ? `needed by ${dependedOnBy}` : null].filter(Boolean).join(", ");
  return [node.name, status, links === "" ? null : links].filter(Boolean).join(". ");
}

// ---- failures ----------------------------------------------------------------------------------

export interface PackageFailure {
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
 * Words for a failed read. The API's message is used only where it is fixed text that names the registry and nothing
 * the registry said; everything else comes from the shared table.
 */
export function packageFailure(error: unknown, registry: PackageRegistry, name: string): PackageFailure {
  const e = (error ?? {}) as Thrown;
  const code = typeof e.code === "string" ? e.code : "network_error";
  const message = typeof e.message === "string" ? e.message : "";
  const requestId = typeof e.requestId === "string" ? e.requestId : null;
  const retryAfter = typeof e.retryAfterSeconds === "number" ? e.retryAfterSeconds : null;
  const where = REGISTRY_NAMES[registry];
  const base = { wait: null, requestId: null, retryable: false } as const;

  if (code === "not_found") {
    return { ...base, title: `No package called ${name} on ${where}`, body: `${where} has no package with that name. Check the spelling, or switch registry if it lives on the other one.` };
  }
  if (code === "validation_error") {
    return { ...base, title: "That is not a package name", body: `${where} would not accept that name. Use just the name, like ${registry === "npm" ? "express or @types/node" : "flask"}.` };
  }
  if (code === "upstream_rate_limited") {
    return {
      title: `${where} is limiting requests`,
      body: `${where} asked VYRON to slow down. Packages already opened stay on the page. Try again after the time below.`,
      wait: describeWait(retryAfter),
      requestId: null,
      retryable: true,
    };
  }
  if (code === "upstream_error") {
    return { title: `${where} did not give a usable answer`, body: sentence(message), wait: null, requestId, retryable: true };
  }
  if (code === "service_unavailable" && /switched off/i.test(message)) {
    return { ...base, title: "Package registries are switched off", body: "This server was started without registry access. Restart it without --no-registries to read packages." };
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

const GITHUB_OWNER = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/;
const GITHUB_REPO = /^[A-Za-z0-9._-]{1,100}$/;

/** If a package homepage points at a GitHub repository, return "owner/repo", else null. */
export function githubRepoFromUrl(raw: string | null): string | null {
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  const host = url.hostname.toLowerCase();
  if (host !== "github.com" && host !== "www.github.com") return null;
  const parts = url.pathname.split("/").filter((part) => part !== "");
  if (parts.length < 2) return null;
  const owner = parts[0] as string;
  const repo = (parts[1] as string).replace(/\.git$/i, "");
  if (!GITHUB_OWNER.test(owner) || !GITHUB_REPO.test(repo) || repo === "." || repo === "..") return null;
  return `${owner}/${repo}`;
}
