/**
 * The dependencies page: asks the same-origin API to read one PyPI or npm package, draws what that package says it
 * depends on, and grows the graph one package at a time when a dependency is opened. Every value is drawn as text
 * (never markup) and the page keeps nothing: no storage, no cookies. The address (`?package=pypi:name`) holds the
 * package the graph started from and is the only state.
 *
 * The graph is a picture of what each package's own metadata lists. A package that has not been opened has no lines
 * out of it yet, which the page says in words, because it is not the same as having no dependencies.
 */
import { VyronClient } from "../../../../packages/api-client/src/client.ts";
import {
  addRead,
  DEFAULT_METRICS,
  dependencyOutline,
  edgePath,
  emptyGraph,
  layoutLayers,
  nodeStatus,
  packageId,
  stepFrom,
} from "../../../../packages/graph/src/dependency-graph.ts";
import type {
  DependencyGraph,
  DepNode,
  LayerLayout,
  LayoutMetrics,
  PlacedNode,
  StepDirection,
} from "../../../../packages/graph/src/dependency-graph.ts";
import type { PackageReport } from "../../../../packages/types/src/api.ts";
import {
  describeGraph,
  describeLeftOut,
  describeNode,
  describeNotDrawn,
  describePackageCache,
  describeRead,
  describeRequirement,
  githubRepoFromUrl,
  isStandIn,
  packageFailure,
  packageLabel,
  readPackageParam,
  REGISTRY_NAMES,
  requestFromInput,
  STAND_IN_NOTE,
  statusHelp,
  STATUS_WORDS,
  writePackageParam,
} from "./dependencies-model.ts";
import type { PackageFailure, PackageRequest } from "./dependencies-model.ts";
import { clear, fill, h, plural, s } from "./dom.ts";
import { downloadJson } from "./export.ts";
import { brandMark } from "./icons.ts";
import { provenanceBadge } from "./marks.ts";
import { newReleaseState, renderReleases } from "./release-view.ts";
import type { ReleaseState, ReleaseView } from "./release-view.ts";
import { describeLoading, releasesFailure, summarizeReleases } from "./releases-model.ts";

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`#${id} is missing from dependencies.html`);
  return node as T;
}

const client = new VyronClient({ baseUrl: "", timeoutMs: 20_000 });
const reading = el<HTMLElement>("reading");
const form = el<HTMLFormElement>("dep-form");
const input = el<HTMLInputElement>("dep-input");
const registryPick = el<HTMLSelectElement>("dep-registry");
const submit = el<HTMLButtonElement>("dep-submit");
const live = el<HTMLElement>("live");
const EXAMPLES: ReadonlyArray<PackageRequest> = [
  { registry: "pypi", name: "flask" },
  { registry: "npm", name: "express" },
];
const AGE_REFRESH_MS = 10_000;
/** Wider than the library default so a package name of about twenty characters fits in its box. */
const METRICS: LayoutMetrics = { ...DEFAULT_METRICS, nodeWidth: 216 };
const NAME_CHARS = 21;

fill(el("brand-mark"), brandMark());

// ---- state: one graph, the answers it was built from, and what is selected ---------------------------

let generation = 0;
let controller: AbortController | null = null;
let ageTimer: number | null = null;
let paintAge: (() => void) | null = null;
let releaseView: ReleaseView | null = null;

let graph: DependencyGraph = emptyGraph();
const reports = new Map<string, PackageReport>();
const inFlight = new Set<string>();
let selected: string | null = null;
let focused: string | null = null;
/** The latest failed attempt to open each package, kept until it is tried again or opens. */
const problems = new Map<string, PackageFailure>();
/** What is known about each package's release history, kept so that moving between packages loses nothing. */
const releaseStates = new Map<string, ReleaseState>();

interface Parts {
  readonly summary: HTMLElement;
  readonly slot: HTMLElement;
  readonly svgHost: HTMLElement;
  readonly details: HTMLElement;
  readonly outline: HTMLElement;
  readonly releases: HTMLElement;
  /** The release section's heading, which focus goes to when the history arrives or fails. */
  readonly releaseHeading: HTMLElement;
}
let parts: Parts | null = null;

function announce(message: string): void {
  live.textContent = message;
}

function stopAgeTimer(): void {
  if (ageTimer !== null) window.clearInterval(ageTimer);
  ageTimer = null;
  paintAge = null;
  releaseView = null;
}

function show(content: HTMLElement, focus: boolean): void {
  clear(reading);
  reading.append(content);
  if (focus) content.querySelector<HTMLElement>("[tabindex='-1']")?.focus({ preventScroll: false });
  document.getElementById("dep-main")?.scrollTo({ top: 0 });
}

/** A link only when it is an http(s) address, so a surprising value can never become a script URL. */
function safeLink(href: string, text: string, className?: string): HTMLElement {
  const web = /^https?:\/\//i.test(href);
  return web ? h("a", { href, rel: "noopener noreferrer", ...(className ? { class: className } : {}) }, text) : h("span", {}, text);
}

function shortName(name: string): string {
  return name.length > NAME_CHARS ? `${name.slice(0, NAME_CHARS - 1)}…` : name;
}

// ---- the quiet states ------------------------------------------------------------------------------

function renderIdle(): void {
  const examples = EXAMPLES.flatMap((example, index) => {
    const button = h("button", { type: "button", class: "text-button example", "data-example": `${example.registry}:${example.name}` }, packageLabel(example.registry, example.name));
    button.addEventListener("click", () => void start(example, "push"));
    return [button, index < EXAMPLES.length - 2 ? ", " : index === EXAMPLES.length - 2 ? " or " : "."];
  });
  show(
    h(
      "div",
      { class: "idle" },
      h("h1", { class: "idle-title", id: "reading-title", tabindex: "-1" }, "Read a package's dependencies"),
      h(
        "p",
        { class: "idle-lede" },
        "Pick PyPI or npm, then type a package name or paste a link to its page. VYRON asks the registry for that one package and draws what it says it depends on. Open a dependency to read its own, and the graph grows one package at a time.",
      ),
      h("p", { class: "idle-examples" }, "Try ", ...examples),
    ),
    false,
  );
  announce("");
}

function renderLoading(label: string): void {
  show(h("p", { class: "state-line", role: "status" }, `Reading ${label}…`), false);
  announce(`Reading ${label}`);
}

function failureBlock(failure: PackageFailure, retry: (() => void) | null, lead: string | null, role: "alert" | "status" | null): HTMLElement {
  const again = retry && failure.retryable ? h("button", { type: "button", class: "text-button" }, "Try again") : null;
  again?.addEventListener("click", retry as () => void);
  return h(
    "div",
    { class: "failure", role },
    h("p", { class: "failure-title" }, lead === null ? failure.title : `${lead} ${failure.title}`),
    failure.body ? h("p", { class: "failure-body" }, failure.body) : null,
    failure.wait ? h("p", { class: "dust" }, failure.wait) : null,
    failure.requestId ? h("p", { class: "dust small" }, `Request ${failure.requestId}.`) : null,
    again,
  );
}

function renderFailure(failure: PackageFailure, retry: (() => void) | null, role: "alert" | "status" = "alert"): void {
  const block = failureBlock(failure, retry, null, role);
  const title = block.querySelector<HTMLElement>(".failure-title");
  title?.setAttribute("id", "reading-title");
  title?.setAttribute("tabindex", "-1");
  show(h("div", {}, block), true);
  announce(`${failure.title}. ${failure.body}`);
}

// ---- drawing the graph -------------------------------------------------------------------------------

function statusMark(node: DepNode, x: number, y: number): SVGElement {
  const status = nodeStatus(node);
  if (status === "unreadable") {
    return s("path", { class: "node-state", "data-status": status, d: `M${x - 3.5} ${y - 3.5}L${x + 3.5} ${y + 3.5}M${x + 3.5} ${y - 3.5}L${x - 3.5} ${y + 3.5}` });
  }
  return s("circle", { class: "node-state", "data-status": status, cx: x, cy: y, r: 4 });
}

function nodeGroup(node: DepNode, at: PlacedNode, tabbable: boolean): SVGGElement {
  const links = [graph.edges.filter((edge) => edge.from === node.id).length, graph.edges.filter((edge) => edge.to === node.id).length] as const;
  const label = `${describeNode(node, links[0], links[1])}${node.id === graph.root ? ". Starting package" : ""}`;
  const report = reports.get(node.id);
  const group = s(
    "g",
    {
      class: "dep-node",
      transform: `translate(${at.x} ${at.y})`,
      role: "button",
      tabindex: tabbable ? 0 : -1,
      "aria-label": label,
      "data-id": node.id,
      "data-status": nodeStatus(node),
    },
    s("title", {}, node.name),
    s("rect", { class: "node-box", width: METRICS.nodeWidth, height: METRICS.nodeHeight, rx: 4 }),
    statusMark(node, 14, METRICS.nodeHeight / 2),
    s("text", { class: "node-name", x: 28, y: METRICS.nodeHeight / 2 + 4 }, shortName(node.name)),
    report ? s("text", { class: "node-count", x: METRICS.nodeWidth - 10, y: METRICS.nodeHeight / 2 + 4, "text-anchor": "end" }, String(report.dependencies.length)) : null,
  );
  if (node.id === graph.root) group.classList.add("is-root");
  if (node.id === selected) {
    group.classList.add("is-selected");
    group.setAttribute("aria-current", "true");
  }
  if (inFlight.has(node.id)) group.setAttribute("aria-busy", "true");
  group.addEventListener("click", () => void choose(node.id, "graph"));
  return group;
}

function drawGraph(layout: LayerLayout): SVGSVGElement {
  const at = new Map(layout.nodes.map((node) => [node.id, node] as const));
  const tabId = focused !== null && graph.nodes.has(focused) ? focused : (selected ?? graph.root);
  const edges = graph.edges.flatMap((edge) => {
    const from = at.get(edge.from);
    const to = at.get(edge.to);
    if (!from || !to) return [];
    const linked = edge.from === selected || edge.to === selected;
    const path = s(
      "path",
      {
        class: "dep-edge",
        d: edgePath(from, to, METRICS),
        "data-readable": edge.readable ? "true" : "false",
        "marker-end": linked ? "url(#dep-arrow-active)" : "url(#dep-arrow)",
      },
      s("title", {}, `${graph.nodes.get(edge.from)?.name ?? edge.from} needs ${graph.nodes.get(edge.to)?.name ?? edge.to}, ${describeRequirement(edge.requirement, edge.readable)}`),
    );
    if (linked) path.classList.add("is-linked");
    return [path];
  });
  const marker = (id: string, className: string): SVGElement =>
    s("marker", { id, viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: "auto" }, s("path", { class: className, d: "M0 0L8 4L0 8Z" }));
  const svg = s(
    "svg",
    {
      class: "dep-svg",
      width: layout.width,
      height: layout.height,
      viewBox: `0 0 ${layout.width} ${layout.height}`,
      role: "group",
      "aria-label": `Dependency graph, ${plural(graph.nodes.size, "package", "packages")}. Arrow keys move between packages and Enter opens one.`,
    },
    s("defs", {}, marker("dep-arrow", "dep-arrow"), marker("dep-arrow-active", "dep-arrow is-linked")),
    s("g", { class: "dep-edges" }, ...edges),
    s("g", { class: "dep-nodes" }, ...[...graph.nodes.values()].flatMap((node) => {
      const place = at.get(node.id);
      return place ? [nodeGroup(node, place, node.id === tabId)] : [];
    })),
  );
  svg.addEventListener("keydown", (event) => onGraphKey(event, layout));
  return svg;
}

const ARROWS: Readonly<Record<string, StepDirection>> = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };

function onGraphKey(event: KeyboardEvent, layout: LayerLayout): void {
  const here = (event.target as Element | null)?.closest<SVGElement>("[data-id]")?.getAttribute("data-id");
  if (!here) return;
  const direction = ARROWS[event.key];
  if (direction) {
    event.preventDefault();
    const next = stepFrom(graph, layout, here, direction);
    if (next !== null) moveFocus(next);
    return;
  }
  if (event.key === "Home" && graph.root !== null) {
    event.preventDefault();
    moveFocus(graph.root);
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    void choose(here, "graph");
  }
}

function nodeElement(id: string): SVGElement | null {
  for (const node of reading.querySelectorAll<SVGElement>(".dep-node")) if (node.getAttribute("data-id") === id) return node;
  return null;
}

function moveFocus(id: string): void {
  const previous = focused !== null ? nodeElement(focused) : null;
  previous?.setAttribute("tabindex", "-1");
  focused = id;
  const next = nodeElement(id);
  if (!next) return;
  next.setAttribute("tabindex", "0");
  next.focus({ preventScroll: true });
  next.scrollIntoView({ block: "nearest", inline: "nearest" });
}

// ---- the details beside the graph and the text outline -----------------------------------------------

function jump(id: string, label: string, className: string): HTMLElement {
  const button = h("button", { type: "button", class: className }, label);
  button.addEventListener("click", () => void choose(id, "details"));
  return button;
}

function detailsFor(node: DepNode): HTMLElement {
  const status = nodeStatus(node);
  const report = reports.get(node.id);
  const busy = inFlight.has(node.id);
  const where = REGISTRY_NAMES[node.registry];

  const failed = problems.get(node.id);
  const head = [
    h("h3", { class: "details-title", id: "details-title", tabindex: "-1" }, node.name),
    h(
      "p",
      { class: "details-status" },
      h("strong", {}, STATUS_WORDS[status]),
      ` ${failed && !busy ? "The last attempt to open it failed." : statusHelp(status, node.registry)}`,
    ),
  ];

  let action: HTMLElement | null = null;
  if (busy) {
    action = h("p", { class: "state-line", role: "status" }, `Reading ${packageLabel(node.registry, node.name)}…`);
  } else if (failed && status === "unread") {
    action = failureBlock(failed, () => void choose(node.id, "details"), null, null);
  } else if (status === "unread") {
    const open = h("button", { type: "button", class: "primary", id: "open-package" }, "Read its dependencies");
    open.addEventListener("click", () => void choose(node.id, "details"));
    action = h("p", { class: "details-actions" }, open);
  }

  const needs = graph.edges.filter((edge) => edge.to === node.id);
  const neededBy =
    needs.length === 0
      ? null
      : h(
          "section",
          { "aria-labelledby": "needed-by-title" },
          h("h4", { class: "small-heading", id: "needed-by-title" }, "Needed by"),
          h(
            "ul",
            { class: "dep-list" },
            ...needs.map((edge) =>
              h("li", {}, jump(edge.from, graph.nodes.get(edge.from)?.name ?? edge.from, "outline-link"), h("span", { class: "outline-meta" }, ` ${describeRequirement(edge.requirement, edge.readable)}`)),
            ),
          ),
        );

  if (!report) return h("div", { class: "details-body" }, ...head, action, neededBy);

  const cache = describePackageCache(report, Date.now());
  const ageText = h("span", { class: "age", "aria-live": "off" }, cache.text);
  const cacheLabel = h("span", { class: "cache-label" }, cache.label);
  const band = h("p", { class: "band-line", "data-tone": cache.tone }, provenanceBadge(report.provenance), cacheLabel, ageText);
  paintAge = () => {
    const fresh = describePackageCache(report, Date.now());
    cacheLabel.textContent = fresh.label;
    ageText.textContent = fresh.text;
    band.setAttribute("data-tone", fresh.tone);
  };

  const dependsOn = report.dependencies.map((dependency) => {
    const drawn = graph.nodes.has(packageId(report.registry, dependency.name));
    return h(
      "li",
      {},
      drawn ? jump(packageId(report.registry, dependency.name), dependency.name, "outline-link") : h("span", { class: "outline-name" }, dependency.name),
      h("span", { class: "outline-meta" }, ` ${describeRequirement(dependency.requirement, dependency.readable)}${drawn ? "" : ", not drawn because the graph is full"}`),
    );
  });
  const leftOut = describeLeftOut(report.left_out);

  const githubRepo = githubRepoFromUrl(report.homepage);
  const compareHref = `/compare.html?mode=packages&a=${encodeURIComponent(`${report.registry}:${report.name}`)}`;
  const exportBtn = h("button", { type: "button", class: "text-button" }, "Export graph JSON");
  exportBtn.addEventListener("click", () => {
    const loadedReleases = [...releaseStates.entries()]
      .filter(([, state]) => state.data !== null)
      .map(([id, state]) => ({ id, releases: state.data }));
    downloadJson(`vyron-${report.registry}-${report.name}`, {
      exported_at: new Date().toISOString(),
      root: graph.root,
      nodes: [...graph.nodes.values()],
      edges: graph.edges,
      reports: [...reports.values()],
      releases: loadedReleases,
    });
    announce(`Exported dependency graph for ${report.name} as JSON.`);
  });

  return h(
    "div",
    { class: "details-body" },
    ...head,
    action,
    report.summary ? h("p", { class: "details-summary" }, report.summary) : null,
    h(
      "dl",
      { class: "ledger" },
      h("div", {}, h("dt", {}, "Version"), h("dd", {}, report.version)),
      h("div", {}, h("dt", {}, "License"), h("dd", {}, report.license ?? "Not stated")),
      h("div", {}, h("dt", {}, "Dependencies listed"), h("dd", {}, String(report.dependencies.length))),
    ),
    h(
      "p",
      { class: "head-links" },
      report.registry_url ? safeLink(report.registry_url, `View on ${where}`, "text-link") : null,
      report.homepage ? safeLink(report.homepage, "Project homepage", "text-link") : null,
      githubRepo ? h("a", { class: "text-link", href: `/repository.html?repo=${encodeURIComponent(githubRepo)}` }, "Inspect GitHub repo") : null,
      h("a", { class: "text-link", href: compareHref }, "Compare package"),
      exportBtn,
    ),
    band,
    h("ul", { class: "evidence", "aria-label": "Read from" }, h("li", {}, safeLink(report.source_url, report.source_url))),
    h(
      "section",
      { "aria-labelledby": "depends-on-title" },
      h("h4", { class: "small-heading", id: "depends-on-title" }, "Depends on"),
      dependsOn.length > 0 ? h("ul", { class: "dep-list" }, ...dependsOn) : h("p", { class: "dust" }, `${report.name} lists no dependencies.`),
    ),
    leftOut.length > 0
      ? h(
          "section",
          { "aria-labelledby": "left-out-title" },
          h("h4", { class: "small-heading", id: "left-out-title" }, "Declared but not drawn"),
          h("ul", { class: "left-out" }, ...leftOut.map((text) => h("li", {}, text))),
          h("p", { class: "note" }, "These are in the package's own metadata. They are left out because they do not apply to everyone who installs it."),
        )
      : null,
    neededBy,
  );
}

function outlineList(layout: LayerLayout): HTMLElement {
  const rows = dependencyOutline(graph, layout);
  const top = h("ul", { class: "outline-list" });
  const lists: HTMLElement[] = [top];
  let last: HTMLElement | null = null;
  for (const row of rows) {
    while (lists.length - 1 > row.level) lists.pop();
    if (lists.length - 1 < row.level && last) {
      const nested = h("ul", { class: "outline-list" });
      last.append(nested);
      lists.push(nested);
    }
    const node = graph.nodes.get(row.id);
    if (!node) continue;
    const meta = [
      row.level === 0 ? "starting package" : describeRequirement(row.requirement, graph.edges.find((edge) => edge.to === row.id)?.readable ?? true),
      STATUS_WORDS[nodeStatus(node)].toLowerCase(),
      row.repeated ? "listed above, so its dependencies are not listed again" : null,
    ].filter(Boolean);
    const item = h("li", {}, jump(row.id, node.name, "outline-link"), h("span", { class: "outline-meta" }, ` ${meta.join("; ")}`));
    (lists[lists.length - 1] as HTMLElement).append(item);
    last = item;
  }
  return top;
}

// ---- keeping the page in step with the graph ---------------------------------------------------------

/** Rebuild the drawing, the details and the outline from the state, and put keyboard focus back where it was. */
function redraw(): void {
  if (!parts || graph.root === null) return;
  const active = document.activeElement;
  const inGraph = active instanceof Element && parts.svgHost.contains(active);
  const inDetails = active instanceof Element && (parts.details.contains(active) || parts.outline.contains(active));
  const layout = layoutLayers(graph, METRICS);

  const summary = describeGraph(graph);
  parts.summary.textContent = summary.text;
  const full = [...graph.nodes.values()].filter((node) => node.notDrawn > 0);
  clear(parts.slot);
  fill(parts.slot, ...full.map((node) => h("p", { class: "note" }, describeNotDrawn(node.name, node.notDrawn))));

  clear(parts.svgHost);
  parts.svgHost.append(drawGraph(layout));
  clear(parts.details);
  const node = selected !== null ? graph.nodes.get(selected) : undefined;
  parts.details.append(node ? detailsFor(node) : h("p", { class: "dust" }, "Select a package to see what is known about it."));
  clear(parts.outline);
  parts.outline.append(outlineList(layout));
  drawReleases();

  if (inGraph && focused !== null) nodeElement(focused)?.focus({ preventScroll: true });
  else if (inDetails) parts.details.querySelector<HTMLElement>("#details-title")?.focus({ preventScroll: true });
}

// ---- the release history of the selected package -------------------------------------------------------

function releaseStateOf(id: string): ReleaseState {
  let state = releaseStates.get(id);
  if (!state) {
    state = newReleaseState();
    releaseStates.set(id, state);
  }
  return state;
}

/** Show the release section for the selected package, from what is known about it. Keeps focus in the section. */
function drawReleases(): void {
  if (!parts) return;
  const node = selected !== null ? graph.nodes.get(selected) : undefined;
  const heading = parts.releaseHeading;
  heading.textContent = node ? `Release history of ${node.name}` : "Release history";
  if (!node) {
    clear(parts.releases);
    releaseView = null;
    return;
  }
  const active = document.activeElement;
  const hadFocus = active instanceof Element && parts.releases.contains(active);
  const id = node.id;
  releaseView = renderReleases(
    parts.releases,
    { registry: node.registry, name: node.name, readable: nodeStatus(node) !== "unreadable" },
    releaseStateOf(id),
    { load: () => void loadReleases(id), rerender: drawReleases, announce },
  );
  if (hadFocus && !parts.releases.contains(document.activeElement)) heading.focus({ preventScroll: true });
}

async function loadReleases(id: string): Promise<void> {
  const node = graph.nodes.get(id);
  if (!node) return;
  const state = releaseStateOf(id);
  if (state.phase === "loading") return;
  state.phase = "loading";
  state.failure = null;
  if (selected === id) drawReleases();
  announce(describeLoading(node.name));
  const token = generation;
  const signal = controller?.signal;
  try {
    const data = await client.get("get_package_releases", { path: { registry: node.registry }, query: { name: node.name }, ...(signal ? { signal } : {}) });
    if (token !== generation) return;
    state.phase = "loaded";
    state.data = data;
    state.selected = data.releases.length - 1;
    if (selected === id) {
      drawReleases();
      parts?.releaseHeading.focus({ preventScroll: false });
    }
    announce(`${node.name}: ${summarizeReleases(data.releases).text}`);
  } catch (error) {
    if (token !== generation || (error as { code?: unknown } | null)?.code === "aborted") return;
    state.phase = "failed";
    state.failure = releasesFailure(error, node.registry, node.name);
    if (selected === id) {
      drawReleases();
      parts?.releaseHeading.focus({ preventScroll: false });
    }
    announce(`${state.failure.title}. ${state.failure.body}`);
  }
}

/**
 * Select a package and, when it has not been opened and the registry can serve it, read it. `from` says where the
 * choice was made, so keyboard focus goes back to the graph or to the details rather than being lost.
 */
async function choose(id: string, from: "graph" | "details"): Promise<void> {
  const node = graph.nodes.get(id);
  if (!node) return;
  const changed = selected !== id;
  selected = id;
  focused = id;
  const needsReading = nodeStatus(node) === "unread" && !inFlight.has(id);
  if (needsReading) inFlight.add(id);
  if (needsReading) problems.delete(id);
  redraw();
  if (from === "details") parts?.details.querySelector<HTMLElement>("#details-title")?.focus({ preventScroll: true });
  if (changed || needsReading) {
    nodeElement(id)?.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (!needsReading) announce(describeNode(node, graph.edges.filter((edge) => edge.from === id).length, graph.edges.filter((edge) => edge.to === id).length));
  }
  if (needsReading) await readOne(node);
}

async function readOne(node: DepNode): Promise<void> {
  const token = generation;
  const signal = controller?.signal;
  const label = packageLabel(node.registry, node.name);
  announce(`Reading ${label}`);
  try {
    const report = await client.get("get_package_dependencies", { path: { registry: node.registry }, query: { name: node.name }, ...(signal ? { signal } : {}) });
    if (token !== generation) return;
    // The graph knows this package by the name it was asked for, which is the name the report should be filed under.
    const filed = packageId(report.registry, report.name) === node.id ? report : { ...report, name: node.name };
    const result = addRead(graph, filed);
    graph = result.graph;
    reports.set(node.id, report);
    inFlight.delete(node.id);
    problems.delete(node.id);
    redraw();
    // The new packages sit in the next column, which may be past the edge of the drawing: bring them into view.
    const firstNew = filed.dependencies.map((dependency) => nodeElement(packageId(filed.registry, dependency.name))).find((found) => found !== null);
    if (result.added > 0) firstNew?.scrollIntoView({ block: "nearest", inline: "nearest" });
    announce(describeRead(report, result.added, result.notDrawn));
  } catch (error) {
    if (token !== generation || (error as { code?: unknown } | null)?.code === "aborted") return;
    inFlight.delete(node.id);
    const failure = packageFailure(error, node.registry, node.name);
    problems.set(node.id, failure);
    redraw();
    announce(`${node.name} could not be opened. ${failure.title}. ${failure.body}`);
  }
}

// ---- the report --------------------------------------------------------------------------------------

function renderReport(report: PackageReport, added: number, notDrawn: number): void {
  const where = REGISTRY_NAMES[report.registry];
  const summary = h("p", { class: "graph-summary" });
  const slot = h("div", { class: "dep-notes" });
  const svgHost = h("div", { class: "graph-scroll" });
  const details = h("aside", { class: "dep-details", "aria-label": "Selected package" });
  const outline = h("div", { class: "outline-body" });
  const releases = h("div", { class: "release-host" });
  const releaseHeading = h("h2", { class: "small-heading", id: "release-title", tabindex: "-1" }, "Release history");
  parts = { summary, slot, svgHost, details, outline, releases, releaseHeading };

  const legend = (["read", "unread", "unreadable"] as const).map((status) =>
    h(
      "li",
      { class: "legend-item" },
      s(
        "svg",
        { class: "legend-mark", width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": "true", focusable: "false" },
        status === "unreadable"
          ? s("path", { class: "node-state", "data-status": status, d: "M3.5 3.5L10.5 10.5M10.5 3.5L3.5 10.5" })
          : s("circle", { class: "node-state", "data-status": status, cx: 7, cy: 7, r: 4 }),
      ),
      h("span", {}, STATUS_WORDS[status]),
    ),
  );

  const tags: HTMLElement[] = [h("li", { class: "tag" }, where), h("li", { class: "tag" }, `Version ${report.version}`)];
  if (report.license) tags.push(h("li", { class: "tag" }, report.license));

  const article = h(
    "article",
    { class: "report" },
    h(
      "header",
      { class: "report-head" },
      h("h1", { class: "dep-name", id: "reading-title", tabindex: "-1" }, report.name),
      report.summary ? h("p", { class: "repo-desc" }, report.summary) : h("p", { class: "repo-desc dust" }, "No description."),
      h("ul", { class: "tags" }, ...tags),
    ),
    h(
      "section",
      { class: "provenance-band", "aria-labelledby": "provenance-title" },
      h("h2", { class: "sr-only", id: "provenance-title" }, "Where this came from"),
      h("p", { class: "band-line" }, provenanceBadge(report.provenance), h("span", { class: "cache-label" }, `Read from ${where} when a package is opened`)),
      h("p", { class: "note" }, `Every package and line below is what that package's own metadata on ${where} says. VYRON adds nothing to it and stores nothing. Select a package to see how old its answer is.`),
      isStandIn(report) ? h("p", { class: "stand-in" }, STAND_IN_NOTE) : null,
    ),
    h(
      "section",
      { class: "dep-graph-block", "aria-labelledby": "graph-title" },
      h("h2", { class: "small-heading", id: "graph-title" }, "Dependency graph"),
      summary,
      slot,
      h("ul", { class: "graph-legend", "aria-label": "What the marks mean" }, ...legend),
      h("div", { class: "dep-split" }, svgHost, details),
      h(
        "section",
        { class: "release-block", "aria-labelledby": "release-title" },
        releaseHeading,
        releases,
      ),
      h(
        "details",
        { class: "dep-outline" },
        h("summary", {}, "Show the graph as a text outline"),
        outline,
      ),
      h("p", { class: "note" }, "Open a package to read its own dependencies; each one is a single request to the registry. Lines run from a package to what it needs. A dashed line is one the package gives as a link or path. The drawing scrolls sideways when it is wider than the screen."),
    ),
  );
  show(article, true);
  redraw();
  stopAgeTimer();
  ageTimer = window.setInterval(() => {
    paintAge?.();
    releaseView?.paintAge();
  }, AGE_REFRESH_MS);
  announce(describeRead(report, added, notDrawn));
}

// ---- the flow ------------------------------------------------------------------------------------------

type AddressMode = "push" | "replace" | "none";

function updateAddress(request: PackageRequest, mode: AddressMode): void {
  if (mode === "none") return;
  const url = `${location.pathname}${writePackageParam(request)}`;
  if (mode === "push" && url !== `${location.pathname}${location.search}`) history.pushState(null, "", url);
  else history.replaceState(null, "", url);
}

function setBusy(busy: boolean): void {
  submit.disabled = busy;
  reading.setAttribute("aria-busy", busy ? "true" : "false");
}

function resetGraph(): void {
  graph = emptyGraph();
  reports.clear();
  inFlight.clear();
  selected = null;
  focused = null;
  problems.clear();
  releaseStates.clear();
  parts = null;
}

async function start(request: PackageRequest, mode: AddressMode): Promise<void> {
  controller?.abort();
  const mine = new AbortController();
  controller = mine;
  const token = ++generation;
  stopAgeTimer();
  resetGraph();
  registryPick.value = request.registry;
  input.value = request.name;
  setBusy(true);
  const label = packageLabel(request.registry, request.name);
  renderLoading(label);
  try {
    const report = await client.get("get_package_dependencies", { path: { registry: request.registry }, query: { name: request.name }, signal: mine.signal });
    if (token !== generation) return;
    const first = addRead(emptyGraph(), report);
    graph = first.graph;
    reports.set(packageId(report.registry, report.name), report);
    selected = graph.root;
    focused = graph.root;
    const root: PackageRequest = { registry: report.registry, name: report.name };
    updateAddress(root, mode);
    registryPick.value = root.registry;
    input.value = root.name;
    renderReport(report, first.added, first.notDrawn);
  } catch (error) {
    if (token !== generation || (error as { code?: unknown } | null)?.code === "aborted") return;
    renderFailure(packageFailure(error, request.registry, request.name), () => void start(request, "none"));
  } finally {
    if (token === generation) setBusy(false);
  }
}

function fromAddress(): void {
  const wanted = readPackageParam(location.search);
  if (wanted === null) {
    controller?.abort();
    generation += 1;
    stopAgeTimer();
    resetGraph();
    input.value = "";
    setBusy(false);
    renderIdle();
    return;
  }
  void start(wanted, "none");
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const resolved = requestFromInput(registryPick.value === "npm" ? "npm" : "pypi", input.value);
  if ("problem" in resolved) {
    controller?.abort();
    generation += 1;
    stopAgeTimer();
    resetGraph();
    renderFailure({ title: "That is not a package name", body: resolved.problem, wait: null, requestId: null, retryable: false }, null, "status");
    return;
  }
  void start(resolved.request, "push");
});
window.addEventListener("popstate", fromAddress);
fromAddress();
