/**
 * The prototype's entry point: loads the atlas from the same-origin API, draws it, and keeps the
 * map, panels, search and address bar in step. All state lives here; the modules it calls draw.
 */
import { VyronClient } from "../../../../packages/api-client/src/client.ts";
import { UNCLUSTERED_ID } from "../../../../packages/graph/src/atlas.ts";
import { computeLayout } from "../../../../packages/graph/src/layout.ts";
import { cleanQuery } from "../../../../packages/graph/src/map-state.ts";
import { buildTimeline, laterThan } from "../../../../packages/graph/src/timeline.ts";
import type { TimelineModel } from "../../../../packages/graph/src/timeline.ts";
import { ENTITY_KIND_VALUES } from "../../../../packages/types/src/api.ts";
import type { EntityKind } from "../../../../packages/types/src/api.ts";
import { emptyState } from "../../../../packages/ui/src/states.ts";
import { kindVisual, provenanceVisual } from "../../../../packages/ui/src/tokens.ts";
import { loadAllEvents, loadAtlas, loadTimeline, searchEntities } from "./data.ts";
import type { AtlasLoad } from "./data.ts";
import { clear, fill, h, plural } from "./dom.ts";
import { downloadJson } from "./export.ts";
import { brandMark, controlIcon } from "./icons.ts";
import { MapView } from "./map.ts";
import type { Selection } from "./map.ts";
import { Dossier, failureText, OutlineView, Rail, SearchBox } from "./panels.ts";
import type { FailureText } from "./panels.ts";
import { describeYearClamp } from "./time-model.ts";
import { TimeStrip } from "./time-strip.ts";
import { readPageState, writePageState } from "./url.ts";
import type { PageState, ViewKey } from "./url.ts";

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`#${id} is missing from index.html`);
  return node as T;
}

const client = new VyronClient({ baseUrl: "" });
const stage = el<HTMLElement>("stage");
const stageState = el<HTMLElement>("stage-state");
const stageHint = el<HTMLElement>("stage-hint");
const urlNotice = el<HTMLElement>("url-notice");
const live = el<HTMLElement>("live");

fill(el("brand-mark"), brandMark());
fill(el("search-icon"), controlIcon("search"));
fill(el("layers-icon"), controlIcon("layers"));
fill(el("rail-close"), controlIcon("close"));

// The full prompt is cut off in the narrow search box of a phone, so a phone gets a short one.
const searchInput = el<HTMLInputElement>("search");
const FULL_PLACEHOLDER = searchInput.placeholder;
const SHORT_PLACEHOLDER = "Search the map";
const phone = window.matchMedia("(max-width: 767.98px)");
function setPlaceholder(): void {
  searchInput.placeholder = phone.matches ? SHORT_PLACEHOLDER : FULL_PLACEHOLDER;
}
phone.addEventListener("change", setPlaceholder);
setPlaceholder();

function announce(message: string): void {
  live.textContent = message;
}

function showFailure(failure: FailureText, retry: () => void): void {
  clear(stageState);
  stageState.className = "state error";
  stageState.setAttribute("role", "alert");
  const button = h("button", { type: "button", class: "text-button" }, "Try again");
  button.addEventListener("click", retry);
  fill(
    stageState,
    h("strong", { class: "state-title" }, failure.title),
    h("span", {}, ` ${failure.body}`),
    failure.wait ? h("span", {}, ` ${failure.wait}`) : null,
    failure.requestId ? h("span", { class: "dust" }, ` Request ${failure.requestId}.`) : null,
    failure.retryable ? button : null,
  );
  stageState.hidden = false;
}

async function start(): Promise<void> {
  stageState.hidden = false;
  stageState.className = "state";
  stageState.setAttribute("role", "status");
  stageState.textContent = "Loading the map…";
  let loaded: AtlasLoad;
  try {
    loaded = await loadAtlas(client);
  } catch (error) {
    showFailure(failureText(error), () => void start());
    return;
  }
  stageState.hidden = true;
  run(loaded);
}

function run(loaded: AtlasLoad): void {
  const { atlas, completeness } = loaded;
  if (atlas.entities.length === 0) {
    const empty = emptyState("empty_graph");
    stageState.className = "state";
    stageState.textContent = `${empty.title}. ${empty.body}`;
    stageState.hidden = false;
    return;
  }

  // ---- footer ---------------------------------------------------------------------------------
  const counts = el("footer-counts");
  fill(
    counts,
    `${plural(atlas.entities.length, "entity", "entities")} · ${plural(atlas.relationships.length, "relationship", "relationships")} · ${plural(atlas.clusters.length, "cluster", "clusters")}`,
  );
  if (!completeness.complete) {
    fill(
      counts,
      h(
        "strong",
        { class: "warn" },
        ` Incomplete: ${completeness.entities.loaded} of ${completeness.entities.expected} entities and ${completeness.relationships.loaded} of ${completeness.relationships.expected} relationships are on the map.`,
      ),
    );
  }
  const footerNotice = el("footer-notice");
  if (atlas.notice) footerNotice.textContent = atlas.notice;

  // ---- parts ----------------------------------------------------------------------------------
  const layout = computeLayout(
    atlas.entities.map((entity) => ({ id: entity.id, group: atlas.clusterOf.get(entity.id) ?? UNCLUSTERED_ID })),
    atlas.relationships.map((r) => ({ source: r.source_id, target: r.target_id })),
  );

  let page: PageState = readPageState(location.search).state;
  let selection: Selection = null;
  let matches: ReadonlySet<string> | null = null;
  let timelineController: AbortController | null = null;
  let pathController: AbortController | null = null;
  // The year slice: what the timeline strip read, and the entities it leaves out as of the chosen year.
  let timeModel: TimelineModel | null = null;
  let later: ReadonlySet<string> = new Set();
  let undated: ReadonlySet<string> = new Set();
  let timeLoad: AbortController | null = null;

  const wide = window.matchMedia("(min-width: 1100px)");
  const medium = window.matchMedia("(min-width: 768px)");

  const dossierHost = el("dossier");
  const rail = new Rail(el("rail-body"), atlas, {
    onCluster: (id) => {
      const same = selection?.kind === "cluster" && selection.id === id;
      select(same ? null : { kind: "cluster", id }, { camera: true, history: "replace" });
      closeRail();
    },
    onToggleKind: (kind) => {
      const next = new Set<EntityKind>(page.map.kinds);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      setKinds(next);
    },
    onClearKinds: () => setKinds(new Set()),
  });
  const dossier = new Dossier(dossierHost, atlas, {
    onEntity: (id) => select({ kind: "entity", id }, { camera: true, history: "push" }),
    onCluster: (id) => select({ kind: "cluster", id }, { camera: true, history: "replace" }),
    onClose: () => select(null, { camera: false, history: "push" }),
    onRetryTimeline: () => {
      if (selection?.kind === "entity") fetchTimeline(selection.id);
    },
    onFindPath: (fromId, toId) => findPath(fromId, toId),
    onClearPath: () => {
      clearPath();
      announce("Path cleared.");
    },
    onExportEntity: (id) => exportEntity(id),
  });
  const outline = new OutlineView(el("outline"), atlas, (id) => select({ kind: "entity", id }, { camera: false, history: "push" }));
  // The strip sits under the map, so it is built first and the map is measured once with its final size.
  el("time").hidden = false;
  const strip = new TimeStrip(
    el("time"),
    atlas,
    {
      onYear: (year, final) => {
        page = { ...page, map: { ...page.map, year } };
        applyTime(false);
        if (final) updateUrl("replace");
      },
      onEntity: (id) => select({ kind: "entity", id }, { camera: true, history: "push" }),
      onRetry: () => loadTime(),
    },
    { expanded: window.matchMedia("(min-width: 768px) and (min-height: 700px)").matches },
  );
  const map = new MapView({
    host: stage,
    atlas,
    layout,
    // Clearing an entity is a step the Back button can undo; a cluster is not in the address, so it adds none.
    // Where the details panel covers part of the map, the map frames the selection in what is left.
    onSelect: (next) => select(next, { camera: next !== null && !wide.matches, history: next?.kind === "cluster" ? "replace" : "push" }),
  });
  const search = new SearchBox(el<HTMLInputElement>("search"), el("search-results"), el("search-status"), atlas, {
    search: (text, signal) => searchEntities(client, text, signal),
    onResults: (text, results) => {
      page = { ...page, map: { ...page.map, query: cleanQuery(text) } };
      matches = results && results.length > 0 ? new Set(results.map((r) => r.id).filter((id) => atlas.byId.has(id))) : null;
      refreshFilters();
      updateUrl("replace");
    },
    onChoose: (id) => {
      const entity = atlas.byId.get(id);
      matches = null;
      page = { ...page, map: { ...page.map, query: "" } };
      if (entity) search.setValue(entity.name);
      refreshFilters();
      select({ kind: "entity", id }, { camera: true, history: "push" });
    },
  });
  el<HTMLFormElement>("search-form").addEventListener("submit", (event) => event.preventDefault());

  // ---- layout around the map ------------------------------------------------------------------
  /** How far the details panel reaches into the map. It may also cover the strip below the map, which is no loss. */
  function updateInsets(): void {
    let changed: boolean;
    if (wide.matches || dossierHost.dataset["empty"] === "true") {
      changed = map.setInsets({ right: 0, bottom: 0 });
    } else {
      const panel = dossierHost.getBoundingClientRect();
      const area = stage.getBoundingClientRect();
      changed = map.setInsets(
        medium.matches ? { right: Math.max(0, area.right - panel.left), bottom: 0 } : { right: 0, bottom: Math.max(0, area.bottom - panel.top) },
      );
    }
    // The panel took or gave back room: frame the selection again in what is left, so it is not under the panel.
    if (changed && selection !== null && !wide.matches) map.focus(selection, false);
    stageHint.hidden = selection !== null || wide.matches || page.view !== "map";
    // On a phone the sheet lies over the strip; what is covered must not take keyboard focus either.
    el("time").inert = !medium.matches && dossierHost.dataset["empty"] !== "true";
  }
  wide.addEventListener("change", updateInsets);
  medium.addEventListener("change", updateInsets);
  new ResizeObserver(updateInsets).observe(dossierHost);

  const layersToggle = el<HTMLButtonElement>("layers-toggle");
  function setRail(open: boolean): void {
    document.body.classList.toggle("rail-open", open);
    layersToggle.setAttribute("aria-expanded", open ? "true" : "false");
  }
  function closeRail(): void {
    if (!wide.matches) setRail(false);
  }
  layersToggle.addEventListener("click", () => setRail(!document.body.classList.contains("rail-open")));
  el("rail-close").addEventListener("click", () => setRail(false));

  // ---- state changes --------------------------------------------------------------------------
  function updateUrl(mode: "push" | "replace" | "none"): void {
    if (mode === "none") return;
    const target = `${location.pathname}${writePageState(page)}`;
    if (target === `${location.pathname}${location.search}`) return;
    if (mode === "push") history.pushState(null, "", target);
    else history.replaceState(null, "", target);
  }

  function refreshFilters(): void {
    const kinds = new Set<EntityKind>(page.map.kinds);
    map.setFilters({ kinds, matches, later, undated });
    rail.setState({ clusterId: selection?.kind === "cluster" ? selection.id : null, kinds });
  }

  function setKinds(kinds: ReadonlySet<EntityKind>): void {
    page = { ...page, map: { ...page.map, kinds: ENTITY_KIND_VALUES.filter((kind) => kinds.has(kind)) } };
    if (selection?.kind === "entity") {
      const entity = atlas.byId.get(selection.id);
      if (entity && kinds.size > 0 && !kinds.has(entity.kind)) select(null, { camera: false, history: "replace" });
    }
    refreshFilters();
    updateUrl("replace");
    announce(kinds.size === 0 ? "Showing every kind." : `Showing ${[...kinds].map((k) => kindVisual(k).label.toLowerCase()).join(", ")} only.`);
  }

  /** The entities left out as of the year in the address, and the strip's slider moved to match. */
  function syncTime(syncStrip = true): void {
    const year = page.map.year;
    later = timeModel !== null && year !== null ? laterThan(timeModel, year) : new Set();
    undated = timeModel !== null && year !== null ? new Set(timeModel.undatedIds) : new Set();
    if (syncStrip) strip.setYear(year);
  }

  /**
   * Make the map show the year in `page.map.year`: leave out what comes after it, and drop a selection that
   * is among those. `strip` is told too unless the strip is where the change came from.
   */
  function applyTime(syncStrip = true): void {
    syncTime(syncStrip);
    if (selection?.kind === "entity" && later.has(selection.id)) select(null, { camera: false, history: "replace" });
    refreshFilters();
  }

  /** Read every dated event once, then place the entities on the strip. The map works without it. */
  function loadTime(): void {
    timeLoad?.abort();
    const controller = new AbortController();
    timeLoad = controller;
    strip.setState({ kind: "loading" });
    loadAllEvents(client, controller.signal)
      .then((read) => {
        if (controller.signal.aborted) return;
        timeModel = buildTimeline(read.events, atlas.entities.map((entity) => entity.id));
        if (timeModel === null) {
          strip.setState({ kind: "empty" });
          return;
        }
        strip.setYear(page.map.year);
        strip.setState({ kind: "ready", model: timeModel, read: { loaded: read.events.length, expected: read.expected, complete: read.complete, notice: read.notice } });
        const requested = page.map.year;
        const used = strip.currentYear;
        if (requested !== null && used !== requested) {
          const clamp = describeYearClamp(requested, timeModel, used);
          page = { ...page, map: { ...page.map, year: used } };
          if (clamp) {
            urlNotice.textContent = clamp;
            urlNotice.hidden = false;
          }
          updateUrl("replace");
        }
        applyTime();
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        timeModel = null;
        strip.setState({ kind: "failed", failure: failureText(error) });
        applyTime();
      });
  }

  function clearPath(): void {
    pathController?.abort();
    pathController = null;
    map.setPath(null);
    dossier.setPath({ kind: "idle" });
  }

  function findPath(fromId: string, toId: string): void {
    pathController?.abort();
    const controller = new AbortController();
    pathController = controller;
    const fromName = atlas.byId.get(fromId)?.name ?? fromId;
    const targetName = atlas.byId.get(toId)?.name ?? toId;
    dossier.setPath({ kind: "loading", targetName });
    client
      .get("graph_path", { query: { from: fromId, to: toId, max_depth: 6 }, signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (selection?.kind !== "entity" || selection.id !== fromId) return;
        dossier.setPath({ kind: "loaded", result, targetName });
        if (result.found && result.entity_ids.length > 0) {
          const nodeIds = new Set(result.entity_ids);
          const edgeKeys = new Set(result.relationships.map((r) => `${r.source_id}|${r.type}|${r.target_id}`));
          map.setPath({ nodeIds, edgeKeys });
          map.focusNodes(result.entity_ids, true);
          announce(`Found a ${plural(result.relationships.length, "step", "steps")} path from ${fromName} to ${targetName}.`);
        } else {
          map.setPath(null);
          announce(`No path found from ${fromName} to ${targetName} within 6 steps.`);
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (selection?.kind !== "entity" || selection.id !== fromId) return;
        map.setPath(null);
        dossier.setPath({ kind: "failed", failure: failureText(error), targetId: toId });
      });
  }

  function exportEntity(id: string): void {
    const entity = atlas.byId.get(id);
    if (!entity) return;
    const clusterId = atlas.clusterOf.get(id);
    const cluster = atlas.clusters.find((c) => c.id === clusterId) ?? null;
    const connections = (atlas.connections.get(id) ?? []).map((c) => ({
      direction: c.direction,
      type: c.relationship.type,
      provenance: c.relationship.provenance,
      other_id: c.otherId,
      other_name: atlas.byId.get(c.otherId)?.name ?? null,
    }));
    downloadJson(`vyron-entity-${entity.slug}`, {
      exported_at: new Date().toISOString(),
      data_notice: atlas.notice,
      entity,
      cluster: cluster ? { id: cluster.id, label: cluster.label, size: cluster.size } : null,
      connections,
    });
    announce(`Exported ${entity.name} as JSON.`);
  }

  function fetchTimeline(id: string): void {
    timelineController?.abort();
    const controller = new AbortController();
    timelineController = controller;
    dossier.setTimeline({ kind: "loading" });
    loadTimeline(client, id, controller.signal)
      .then((events) => {
        if (selection?.kind === "entity" && selection.id === id) dossier.setTimeline({ kind: "loaded", events });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (selection?.kind === "entity" && selection.id === id) dossier.setTimeline({ kind: "failed", failure: failureText(error) });
      });
  }

  function describe(next: Selection): string {
    if (next === null) return "Selection cleared.";
    if (next.kind === "cluster") {
      const cluster = atlas.clusters.find((c) => c.id === next.id);
      return cluster ? `${cluster.label} cluster, ${plural(cluster.size, "entity", "entities")}.` : "Cluster selected.";
    }
    const entity = atlas.byId.get(next.id);
    if (!entity) return "Entity not found.";
    const n = atlas.connections.get(entity.id)?.length ?? 0;
    return `${entity.name}, ${kindVisual(entity.kind).label.toLowerCase()}. ${plural(n, "connection", "connections")}. ${provenanceVisual(entity.provenance).label}.`;
  }

  function select(next: Selection, options: { camera: boolean; history: "push" | "replace" | "none" }): void {
    urlNotice.hidden = true;
    // Picking an entity that comes after the chosen year (from search, say) shows every year, not a map without it.
    let widened: number | null = null;
    if (next?.kind === "entity" && later.has(next.id)) {
      widened = page.map.year;
      page = { ...page, map: { ...page.map, year: null } };
      later = new Set();
      undated = new Set();
      strip.setYear(null);
    }
    selection = next;
    page = { ...page, map: { ...page.map, focus: next?.kind === "entity" ? next.id : null } };
    timelineController?.abort();
    pathController?.abort();
    pathController = null;
    map.setPath(null);

    dossierHost.dataset["empty"] = next === null ? "true" : "false";
    if (next === null) {
      dossier.showIntro();
    } else if (next.kind === "cluster") {
      const cluster = atlas.clusters.find((c) => c.id === next.id);
      if (cluster) dossier.showCluster(cluster);
      else dossier.showMissing();
    } else {
      const entity = atlas.byId.get(next.id);
      if (entity) {
        dossier.showEntity(entity);
        fetchTimeline(entity.id);
      } else {
        dossier.showMissing();
      }
    }
    const entity = next?.kind === "entity" ? atlas.byId.get(next.id) : undefined;
    document.title = entity ? `${entity.name} · VYRON map prototype` : "VYRON map prototype";
    outline.setSelected(next?.kind === "entity" ? next.id : null);
    map.setSelection(next);
    refreshFilters();
    updateInsets();
    if (options.camera) map.focus(next);
    updateUrl(widened !== null && options.history === "none" ? "replace" : options.history);
    announce(widened === null ? describe(next) : `${describe(next)} Showing every year, because it first appears after ${widened}.`);
  }

  function setView(view: ViewKey, history: "push" | "replace" | "none"): void {
    page = { ...page, view };
    stage.hidden = view !== "map";
    el("outline").hidden = view !== "outline";
    el("time").hidden = view !== "map";
    el("view-map").setAttribute("aria-pressed", view === "map" ? "true" : "false");
    el("view-outline").setAttribute("aria-pressed", view === "outline" ? "true" : "false");
    updateInsets();
    updateUrl(history);
  }
  el("view-map").addEventListener("click", () => setView("map", "replace"));
  el("view-outline").addEventListener("click", () => setView("outline", "replace"));

  /** Make the page match an address: the first load, and the back and forward buttons. */
  function applyAddress(initial: boolean): void {
    const parsed = readPageState(location.search);
    page = parsed.state;
    syncTime();
    const focus = page.map.focus;
    const known = focus !== null && atlas.byId.has(focus);
    setView(page.view, "none");
    search.setValue("");
    matches = null;
    if (page.map.query !== "") search.setText(page.map.query);
    const next: Selection = known && focus !== null ? { kind: "entity", id: focus } : null;
    select(next, { camera: next !== null, history: "none" });
    if (focus !== null && !known) {
      dossier.showMissing();
      dossierHost.dataset["empty"] = "false";
      page = { ...page, map: { ...page.map, focus: null } };
      updateInsets();
      updateUrl("replace");
    }
    refreshFilters();
    // After `select`, which hides the notice because any later action makes it stale.
    if (initial && parsed.issues.length > 0) {
      urlNotice.textContent = `Part of this link could not be used and was ignored: ${parsed.issues.join("; ")}.`;
      urlNotice.hidden = false;
      updateUrl("replace"); // so a copied address no longer carries what was dropped
    }
  }
  window.addEventListener("popstate", () => applyAddress(false));

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const typing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      search.focus();
    } else if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      search.focus();
    } else if (event.key === "Escape" && document.body.classList.contains("rail-open")) {
      setRail(false);
    }
  });

  applyAddress(true);
  loadTime();
}

void start();
