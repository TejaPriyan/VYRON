/**
 * Side-by-side compare page: reads two PyPI/npm packages or two public GitHub repositories through the
 * same-origin API, compares their figures and overlapping dependencies/languages, and carries provenance
 * and exact source URLs for both sides. Nothing is stored in the browser.
 */
import { VyronClient } from "../../../../packages/api-client/src/client.ts";
import type { PackageReleases, PackageReport, RepositoryAnalysis } from "../../../../packages/types/src/api.ts";
import {
  comparePackageRows,
  compareRepoRows,
  diffDependencies,
  diffLanguages,
  readCompareParams,
  writeCompareParams,
} from "./compare-model.ts";
import type { CompareMode, CompareRow } from "./compare-model.ts";
import {
  describePackageCache,
  isStandIn,
  packageFailure,
  REGISTRY_NAMES,
  requestFromInput,
  STAND_IN_NOTE,
} from "./dependencies-model.ts";
import { clear, fill, h, plural } from "./dom.ts";
import { downloadJson } from "./export.ts";
import { brandMark } from "./icons.ts";
import { provenanceBadge } from "./marks.ts";
import { describeCache, repositoryFailure, requestFromLink } from "./repository-model.ts";

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`#${id} is missing from compare.html`);
  return node as T;
}

const client = new VyronClient({ baseUrl: "", timeoutMs: 30_000 });
const reading = el<HTMLElement>("reading");
const form = el<HTMLFormElement>("compare-form");
const modePick = el<HTMLSelectElement>("compare-mode");
const inputA = el<HTMLInputElement>("compare-a");
const inputB = el<HTMLInputElement>("compare-b");
const submit = el<HTMLButtonElement>("compare-submit");
const live = el<HTMLElement>("live");

fill(el("brand-mark"), brandMark());

let generation = 0;
let controller: AbortController | null = null;
let ageTimer: number | null = null;
let agePainters: Array<() => void> = [];

function announce(message: string): void {
  live.textContent = message;
}

function stopAgeTimer(): void {
  if (ageTimer !== null) window.clearInterval(ageTimer);
  ageTimer = null;
  agePainters = [];
}

function startAgeTimer(): void {
  if (agePainters.length === 0) return;
  ageTimer = window.setInterval(() => {
    for (const paint of agePainters) paint();
  }, 10_000);
}

function safeLink(href: string, label: string, className = "evidence-link"): HTMLElement {
  try {
    const parsed = new URL(href);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") {
      return h("a", { class: className, href: parsed.href, target: "_blank", rel: "noopener noreferrer" }, label);
    }
  } catch {
    // non-URL text stays text
  }
  return h("span", { class: className }, label);
}

function updatePlaceholders(mode: CompareMode): void {
  if (mode === "repos") {
    inputA.placeholder = "pallets/flask";
    inputB.placeholder = "django/django";
  } else {
    inputA.placeholder = "pypi:flask or npm:express";
    inputB.placeholder = "pypi:django or npm:fastify";
  }
}

function renderIdle(prefillMode: CompareMode): void {
  stopAgeTimer();
  document.title = "VYRON side-by-side compare";
  const exampleButton = (mode: CompareMode, a: string, b: string, label: string): HTMLButtonElement => {
    const btn = h("button", { type: "button", class: "text-button example" }, label);
    btn.addEventListener("click", () => {
      modePick.value = mode;
      updatePlaceholders(mode);
      inputA.value = a;
      inputB.value = b;
      void runCompare(mode, a, b, "push");
    });
    return btn;
  };
  clear(reading);
  fill(
    reading,
    h(
      "div",
      { class: "idle" },
      h("h1", { class: "idle-title", id: "compare-title", tabindex: "-1" }, "Compare side by side"),
      h(
        "p",
        { class: "idle-lede" },
        "Pick Packages or GitHub repositories, enter two targets, and VYRON reads both live through the API. Nothing is scored, ranked or stored: every figure carries its source and age.",
      ),
      h(
        "p",
        { class: "idle-examples" },
        "Try ",
        exampleButton("packages", "pypi:flask", "pypi:django", "Flask vs Django (PyPI)"),
        ", ",
        exampleButton("packages", "npm:express", "npm:fastify", "Express vs Fastify (npm)"),
        " or ",
        exampleButton("repos", "pallets/flask", "pallets/werkzeug", "pallets/flask vs pallets/werkzeug"),
        ".",
      ),
      h(
        "p",
        { class: "note" },
        prefillMode === "repos"
          ? "Each GitHub repository uses two requests from the server's hourly quota (four for a pair)."
          : "Prefix packages with pypi: or npm:, or paste links to their registry pages.",
      ),
    ),
  );
}

function renderTable(caption: string, colA: string, colB: string, rows: ReadonlyArray<CompareRow>): HTMLElement {
  return h(
    "div",
    { class: "compare-table-wrap", tabindex: "0", role: "region", "aria-labelledby": "compare-table-caption" },
    h(
      "table",
      { class: "compare-table" },
      h("caption", { class: "compare-caption", id: "compare-table-caption" }, caption),
      h(
        "thead",
        {},
        h("tr", {}, h("th", { scope: "col" }, "Field"), h("th", { scope: "col" }, colA), h("th", { scope: "col" }, colB)),
      ),
      h(
        "tbody",
        {},
        ...rows.map((row) =>
          h("tr", {}, h("th", { scope: "row" }, row.label), h("td", {}, row.a), h("td", {}, row.b)),
        ),
      ),
    ),
  );
}

function renderFailureCard(label: string, title: string, body: string): HTMLElement {
  return h(
    "div",
    { class: "compare-card failure", role: "alert" },
    h("p", { class: "compare-card-label" }, label),
    h("p", { class: "failure-title" }, title),
    body ? h("p", { class: "failure-body" }, body) : null,
  );
}

function packageProvenanceCard(report: PackageReport): HTMLElement {
  const ageText = h("span", { class: "age", "aria-live": "off" });
  const cacheLabel = h("span", { class: "cache-label" });
  const band = h("p", { class: "band-line" }, provenanceBadge(report.provenance), cacheLabel, ageText);
  const paint = (): void => {
    const cache = describePackageCache(report, Date.now());
    cacheLabel.textContent = cache.label;
    ageText.textContent = cache.text;
    band.setAttribute("data-tone", cache.tone);
  };
  paint();
  agePainters.push(paint);
  const depHref = `/dependencies.html?package=${encodeURIComponent(`${report.registry}:${report.name}`)}`;
  return h(
    "article",
    { class: "compare-card" },
    h("h2", { class: "compare-card-title" }, `${report.name} (${REGISTRY_NAMES[report.registry]})`),
    report.summary ? h("p", { class: "details-summary" }, report.summary) : null,
    band,
    isStandIn(report) ? h("p", { class: "stand-in" }, STAND_IN_NOTE) : null,
    h("ul", { class: "evidence", "aria-label": "Read from" }, h("li", {}, safeLink(report.source_url, report.source_url))),
    h(
      "p",
      { class: "head-links" },
      h("a", { class: "text-link", href: depHref }, "Open dependency graph"),
      report.registry_url ? safeLink(report.registry_url, `View on ${REGISTRY_NAMES[report.registry]}`, "text-link") : null,
    ),
  );
}

function repoProvenanceCard(analysis: RepositoryAnalysis): HTMLElement {
  const ageText = h("span", { class: "age", "aria-live": "off" });
  const cacheLabel = h("span", { class: "cache-label" });
  const band = h("p", { class: "band-line" }, provenanceBadge(analysis.provenance), cacheLabel, ageText);
  const paint = (): void => {
    const cache = describeCache(analysis.cache_status, analysis.fetched_at, Date.now());
    cacheLabel.textContent = cache.label;
    ageText.textContent = cache.text;
    band.setAttribute("data-tone", cache.tone);
  };
  paint();
  agePainters.push(paint);
  const repoHref = `/repository.html?repo=${encodeURIComponent(analysis.full_name)}`;
  return h(
    "article",
    { class: "compare-card" },
    h("h2", { class: "compare-card-title" }, analysis.full_name),
    analysis.description ? h("p", { class: "details-summary" }, analysis.description) : h("p", { class: "dust" }, "No description."),
    band,
    h("ul", { class: "evidence", "aria-label": "Read from" }, ...analysis.source_urls.map((url) => h("li", {}, safeLink(url, url)))),
    h(
      "p",
      { class: "head-links" },
      h("a", { class: "text-link", href: repoHref }, "Open full repository reading"),
      safeLink(analysis.html_url, "View on GitHub", "text-link"),
    ),
  );
}

interface PackageSide {
  readonly report: PackageReport;
  readonly releases: PackageReleases | null;
}

async function fetchPackageSide(raw: string, signal: AbortSignal): Promise<{ ok: PackageSide } | { errorTitle: string; errorBody: string }> {
  const parsed = requestFromInput("pypi", raw);
  if ("problem" in parsed) return { errorTitle: "Invalid package input", errorBody: parsed.problem };
  const { registry, name } = parsed.request;
  try {
    const report = await client.get("get_package_dependencies", { path: { registry }, query: { name }, signal });
    let releases: PackageReleases | null = null;
    try {
      releases = await client.get("get_package_releases", { path: { registry }, query: { name }, signal });
    } catch {
      releases = null;
    }
    return { ok: { report, releases } };
  } catch (error) {
    if ((error as { code?: unknown } | null)?.code === "aborted") throw error;
    const failure = packageFailure(error, registry, name);
    return { errorTitle: failure.title, errorBody: failure.body };
  }
}

async function fetchRepoSide(raw: string, signal: AbortSignal): Promise<{ ok: RepositoryAnalysis } | { errorTitle: string; errorBody: string }> {
  try {
    const parsedTarget = await client.get("github_parse", { query: { input: raw.trim() }, signal });
    const req = requestFromLink(parsedTarget);
    if ("problem" in req) return { errorTitle: "Not a repository reference", errorBody: req.problem };
    const { owner, repo } = req.request;
    const analysis = await client.get("get_repository_analysis", { path: { owner, repo }, signal });
    return { ok: analysis };
  } catch (error) {
    if ((error as { code?: unknown } | null)?.code === "aborted") throw error;
    const failure = repositoryFailure(error, raw.trim());
    return { errorTitle: failure.title, errorBody: failure.body };
  }
}

async function runCompare(mode: CompareMode, rawA: string, rawB: string, historyMode: "push" | "replace" | "none"): Promise<void> {
  const a = rawA.trim();
  const b = rawB.trim();
  if (a === "" || b === "") {
    clear(reading);
    fill(
      reading,
      renderFailureCard("Missing input", "Enter two items to compare", "Fill in both fields above, or pick an example below."),
    );
    return;
  }

  controller?.abort();
  stopAgeTimer();
  const current = new AbortController();
  controller = current;
  generation += 1;
  const token = generation;

  if (historyMode !== "none") {
    const target = `${location.pathname}${writeCompareParams({ mode, a, b })}`;
    if (target !== `${location.pathname}${location.search}`) {
      if (historyMode === "push") history.pushState(null, "", target);
      else history.replaceState(null, "", target);
    }
  }

  submit.disabled = true;
  clear(reading);
  fill(reading, h("p", { class: "state-line", role: "status" }, `Reading ${a} and ${b}…`));
  announce(`Comparing ${a} and ${b}.`);

  try {
    if (mode === "packages") {
      const [resA, resB] = await Promise.all([fetchPackageSide(a, current.signal), fetchPackageSide(b, current.signal)]);
      if (token !== generation) return;
      renderPackageComparison(a, b, resA, resB);
    } else {
      const [resA, resB] = await Promise.all([fetchRepoSide(a, current.signal), fetchRepoSide(b, current.signal)]);
      if (token !== generation) return;
      renderRepoComparison(a, b, resA, resB);
    }
    startAgeTimer();
    reading.querySelector<HTMLElement>("#compare-result-title")?.focus({ preventScroll: false });
  } catch (error) {
    if ((error as { code?: unknown } | null)?.code === "aborted") return;
    clear(reading);
    fill(reading, renderFailureCard("Comparison failed", "Could not complete the comparison", "Try again in a moment."));
  } finally {
    if (token === generation) submit.disabled = false;
  }
}

function renderPackageComparison(
  labelA: string,
  labelB: string,
  resA: { ok: PackageSide } | { errorTitle: string; errorBody: string },
  resB: { ok: PackageSide } | { errorTitle: string; errorBody: string },
): void {
  const titleA = "ok" in resA ? `${resA.ok.report.name} (${REGISTRY_NAMES[resA.ok.report.registry]})` : labelA;
  const titleB = "ok" in resB ? `${resB.ok.report.name} (${REGISTRY_NAMES[resB.ok.report.registry]})` : labelB;
  document.title = `${titleA} vs ${titleB} · VYRON compare`;

  const cards = h(
    "div",
    { class: "compare-grid" },
    "ok" in resA ? packageProvenanceCard(resA.ok.report) : renderFailureCard(labelA, resA.errorTitle, resA.errorBody),
    "ok" in resB ? packageProvenanceCard(resB.ok.report) : renderFailureCard(labelB, resB.errorTitle, resB.errorBody),
  );

  let comparison: HTMLElement | null = null;
  let diffSection: HTMLElement | null = null;
  let exportBtn: HTMLButtonElement | null = null;

  if ("ok" in resA && "ok" in resB) {
    const rows = comparePackageRows(resA.ok.report, resB.ok.report, resA.ok.releases, resB.ok.releases);
    comparison = renderTable(`Side-by-side metadata for ${titleA} and ${titleB}`, titleA, titleB, rows);
    const diff = diffDependencies(resA.ok.report, resB.ok.report);
    diffSection = h(
      "section",
      { class: "compare-diff", "aria-labelledby": "compare-diff-title" },
      h("h2", { class: "small-heading", id: "compare-diff-title" }, "Direct dependency overlap"),
      h(
        "p",
        { class: "note" },
        `${plural(diff.shared.length, "shared dependency", "shared dependencies")}, ${diff.onlyA.length} only in ${resA.ok.report.name}, ${diff.onlyB.length} only in ${resB.ok.report.name}.`,
      ),
      h(
        "div",
        { class: "compare-diff-grid" },
        h(
          "div",
          { class: "compare-diff-col" },
          h("h3", { class: "small-heading" }, `Shared (${diff.shared.length})`),
          diff.shared.length === 0
            ? h("p", { class: "dust" }, "No shared direct dependencies listed.")
            : h(
                "ul",
                { class: "compare-list" },
                ...diff.shared.map((item) => h("li", {}, h("strong", {}, item.name), h("span", { class: "dust" }, ` (${item.reqA} vs ${item.reqB})`))),
              ),
        ),
        h(
          "div",
          { class: "compare-diff-col" },
          h("h3", { class: "small-heading" }, `Only in ${resA.ok.report.name} (${diff.onlyA.length})`),
          diff.onlyA.length === 0
            ? h("p", { class: "dust" }, "None.")
            : h(
                "ul",
                { class: "compare-list" },
                ...diff.onlyA.map((item) => h("li", {}, item.name, h("span", { class: "dust" }, ` ${item.req}`))),
              ),
        ),
        h(
          "div",
          { class: "compare-diff-col" },
          h("h3", { class: "small-heading" }, `Only in ${resB.ok.report.name} (${diff.onlyB.length})`),
          diff.onlyB.length === 0
            ? h("p", { class: "dust" }, "None.")
            : h(
                "ul",
                { class: "compare-list" },
                ...diff.onlyB.map((item) => h("li", {}, item.name, h("span", { class: "dust" }, ` ${item.req}`))),
              ),
        ),
      ),
    );
    exportBtn = h("button", { type: "button", class: "text-button" }, "Export comparison JSON");
    exportBtn.addEventListener("click", () => {
      downloadJson(`vyron-compare-${resA.ok.report.name}-${resB.ok.report.name}`, {
        exported_at: new Date().toISOString(),
        mode: "packages",
        left: resA.ok,
        right: resB.ok,
        dependency_diff: diff,
      });
      announce("Exported package comparison as JSON.");
    });
  }

  clear(reading);
  fill(
    reading,
    h(
      "div",
      { class: "compare-head" },
      h("h1", { class: "repo-name", id: "compare-result-title", tabindex: "-1" }, `${titleA} vs ${titleB}`),
      exportBtn,
    ),
    cards,
    comparison,
    diffSection,
  );
  announce(`Loaded comparison of ${titleA} and ${titleB}.`);
}

function renderRepoComparison(
  labelA: string,
  labelB: string,
  resA: { ok: RepositoryAnalysis } | { errorTitle: string; errorBody: string },
  resB: { ok: RepositoryAnalysis } | { errorTitle: string; errorBody: string },
): void {
  const titleA = "ok" in resA ? resA.ok.full_name : labelA;
  const titleB = "ok" in resB ? resB.ok.full_name : labelB;
  document.title = `${titleA} vs ${titleB} · VYRON compare`;

  const cards = h(
    "div",
    { class: "compare-grid" },
    "ok" in resA ? repoProvenanceCard(resA.ok) : renderFailureCard(labelA, resA.errorTitle, resA.errorBody),
    "ok" in resB ? repoProvenanceCard(resB.ok) : renderFailureCard(labelB, resB.errorTitle, resB.errorBody),
  );

  let comparison: HTMLElement | null = null;
  let langSection: HTMLElement | null = null;
  let exportBtn: HTMLButtonElement | null = null;

  if ("ok" in resA && "ok" in resB) {
    const rows = compareRepoRows(resA.ok, resB.ok);
    comparison = renderTable(`Side-by-side GitHub figures for ${titleA} and ${titleB}`, titleA, titleB, rows);
    const langs = diffLanguages(resA.ok, resB.ok);
    langSection = h(
      "section",
      { class: "compare-diff", "aria-labelledby": "compare-langs-title" },
      h("h2", { class: "small-heading", id: "compare-langs-title" }, "Language overlap (by bytes reported by GitHub)"),
      h(
        "div",
        { class: "compare-diff-grid" },
        h(
          "div",
          { class: "compare-diff-col" },
          h("h3", { class: "small-heading" }, `Shared (${langs.shared.length})`),
          langs.shared.length === 0
            ? h("p", { class: "dust" }, "No shared languages reported.")
            : h(
                "ul",
                { class: "compare-list" },
                ...langs.shared.map((item) => h("li", {}, h("strong", {}, item.name), h("span", { class: "dust" }, ` (${item.pctA} vs ${item.pctB})`))),
              ),
        ),
        h(
          "div",
          { class: "compare-diff-col" },
          h("h3", { class: "small-heading" }, `Only in ${titleA} (${langs.onlyA.length})`),
          langs.onlyA.length === 0
            ? h("p", { class: "dust" }, "None.")
            : h("ul", { class: "compare-list" }, ...langs.onlyA.map((item) => h("li", {}, item))),
        ),
        h(
          "div",
          { class: "compare-diff-col" },
          h("h3", { class: "small-heading" }, `Only in ${titleB} (${langs.onlyB.length})`),
          langs.onlyB.length === 0
            ? h("p", { class: "dust" }, "None.")
            : h("ul", { class: "compare-list" }, ...langs.onlyB.map((item) => h("li", {}, item))),
        ),
      ),
    );
    exportBtn = h("button", { type: "button", class: "text-button" }, "Export comparison JSON");
    exportBtn.addEventListener("click", () => {
      downloadJson(`vyron-compare-${resA.ok.full_name}-${resB.ok.full_name}`, {
        exported_at: new Date().toISOString(),
        mode: "repos",
        left: resA.ok,
        right: resB.ok,
        language_diff: langs,
      });
      announce("Exported repository comparison as JSON.");
    });
  }

  clear(reading);
  fill(
    reading,
    h(
      "div",
      { class: "compare-head" },
      h("h1", { class: "repo-name", id: "compare-result-title", tabindex: "-1" }, `${titleA} vs ${titleB}`),
      exportBtn,
    ),
    cards,
    comparison,
    langSection,
  );
  announce(`Loaded comparison of ${titleA} and ${titleB}.`);
}

modePick.addEventListener("change", () => {
  updatePlaceholders(modePick.value === "repos" ? "repos" : "packages");
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const mode: CompareMode = modePick.value === "repos" ? "repos" : "packages";
  void runCompare(mode, inputA.value, inputB.value, "push");
});

function applyAddress(): void {
  const params = readCompareParams(location.search);
  modePick.value = params.mode;
  updatePlaceholders(params.mode);
  inputA.value = params.a ?? "";
  inputB.value = params.b ?? "";
  if (params.a && params.b) {
    void runCompare(params.mode, params.a, params.b, "none");
  } else {
    renderIdle(params.mode);
  }
}

window.addEventListener("popstate", applyAddress);
applyAddress();
