/**
 * The panels around the map: the rail (clusters, kinds, line styles), the details panel (the
 * "dossier"), the text outline and the search box. They show what the atlas holds and what the API
 * returned, in plain words; nothing is generated here.
 */
import type { Atlas, AtlasCluster } from "../../../../packages/graph/src/atlas.ts";
import { buildOutline, RELATION_PHRASES } from "../../../../packages/graph/src/outline.ts";
import type { Entity, EntityKind, PathResult, Provenance, TimelineEvent } from "../../../../packages/types/src/api.ts";
import { errorState, emptyState, describeWait } from "../../../../packages/ui/src/states.ts";
import type { ErrorCodeKey } from "../../../../packages/ui/src/states.ts";
import { kindVisual, provenanceVisual, tokens } from "../../../../packages/ui/src/tokens.ts";
import { clear, fill, h, plural } from "./dom.ts";
import { describeRank, formatEventDate, splitSentence } from "./format.ts";
import { controlIcon } from "./icons.ts";
import { groupSwatch, kindGlyph, lineSample, PROVENANCE_HELP, provenanceBadge } from "./marks.ts";

// ---------------------------------------------------------------------------------------------
// Failure text

export interface FailureText {
  readonly title: string;
  readonly body: string;
  readonly wait: string | null;
  readonly requestId: string | null;
  readonly retryable: boolean;
}

/** Words for any thrown value, using the shared table so every error code has copy. */
export function failureText(error: unknown): FailureText {
  const candidate = error as { code?: unknown; retryAfterSeconds?: unknown; requestId?: unknown; retryable?: unknown } | null;
  const code = (typeof candidate?.code === "string" ? candidate.code : "network_error") as ErrorCodeKey;
  const state = errorState(code) ?? errorState("network_error");
  const wait = describeWait(typeof candidate?.retryAfterSeconds === "number" ? candidate.retryAfterSeconds : null);
  const requestId = state.showRequestId && typeof candidate?.requestId === "string" ? candidate.requestId : null;
  return { title: state.title, body: state.body, wait, requestId, retryable: candidate?.retryable === true || state.action === "retry" };
}

// ---------------------------------------------------------------------------------------------
// Rail

export interface RailHandlers {
  readonly onCluster: (id: string) => void;
  readonly onToggleKind: (kind: EntityKind) => void;
  readonly onClearKinds: () => void;
}

export class Rail {
  private readonly clusterButtons = new Map<string, HTMLButtonElement>();
  private readonly kindButtons = new Map<EntityKind, HTMLButtonElement>();
  private readonly reset: HTMLButtonElement;
  private readonly atlas: Atlas;

  constructor(host: HTMLElement, atlas: Atlas, handlers: RailHandlers) {
    this.atlas = atlas;
    clear(host);
    const clusterList = h("ul", { class: "rows" });
    for (const cluster of atlas.clusters) {
      const members = cluster.memberIds.slice(0, 8).map((id) => {
        const entity = atlas.byId.get(id);
        return entity ? kindGlyph(entity.kind, 12) : null;
      });
      const button = h(
        "button",
        { type: "button", class: "row", "aria-pressed": "false", "data-cluster": cluster.id },
        h("span", { class: "row-main" }, h("span", { class: "row-name" }, cluster.label), h("span", { class: "row-figure" }, String(cluster.size))),
        h("span", { class: "row-glyphs", "aria-hidden": "true" }, ...members),
      );
      button.addEventListener("click", () => handlers.onCluster(cluster.id));
      this.clusterButtons.set(cluster.id, button);
      clusterList.append(h("li", {}, button));
    }

    this.reset = h("button", { type: "button", class: "text-button", hidden: true }, "Show every kind");
    this.reset.addEventListener("click", () => handlers.onClearKinds());
    const kindSection = h("div", { class: "kind-groups" });
    for (const group of tokens.groupOrder) {
      const counts = atlas.kindCounts.filter((entry) => kindVisual(entry.kind).group === group);
      if (counts.length === 0) continue;
      const list = h("ul", { class: "rows" });
      for (const { kind, count } of counts) {
        const button = h(
          "button",
          { type: "button", class: "row kind-row", "aria-pressed": "false", "data-kind": kind },
          h("span", { class: "row-main" }, h("span", { class: "row-name" }, kindGlyph(kind, 14), kindVisual(kind).label), h("span", { class: "row-figure" }, String(count))),
        );
        button.addEventListener("click", () => handlers.onToggleKind(kind));
        this.kindButtons.set(kind, button);
        list.append(h("li", {}, button));
      }
      kindSection.append(h("div", { class: "kind-group" }, h("h3", { class: "group-title" }, groupSwatch(group), tokens.groups[group].label), list));
    }

    fill(host, 
      h(
        "section",
        { class: "rail-section", "aria-labelledby": "rail-clusters" },
        h("h2", { id: "rail-clusters" }, "Clusters"),
        clusterList,
        h("p", { class: "note" }, "Communities found automatically in the relationships. Each is named after its most central member."),
      ),
      h("section", { class: "rail-section", "aria-labelledby": "rail-kinds" }, h("div", { class: "section-head" }, h("h2", { id: "rail-kinds" }, "Show"), this.reset), kindSection),
      this.lineLegend(),
    );
  }

  private lineLegend(): HTMLElement {
    const used = new Set<Provenance>();
    for (const relationship of this.atlas.relationships) used.add(relationship.provenance);
    for (const entity of this.atlas.entities) used.add(entity.provenance);
    const rows = [...used].sort().map((provenance) => {
      const visual = provenanceVisual(provenance);
      return h("li", { class: "legend-row" }, lineSample(visual.dash), h("span", {}, h("strong", {}, visual.label), ` ${PROVENANCE_HELP[provenance]}`));
    });
    rows.push(h("li", { class: "legend-row" }, lineSample(tokens.provenanceStyles.derived.dash), h("span", {}, h("strong", {}, "Cluster outline"), " Computed from the relationships, not curated.")));
    return h("section", { class: "rail-section", "aria-labelledby": "rail-lines" }, h("h2", { id: "rail-lines" }, "How lines are drawn"), h("ul", { class: "legend" }, ...rows), h("p", { class: "note" }, "A solid line would mean a sourced fact. Every line here is dashed, because all of this data is for development."));
  }

  setState(state: { readonly clusterId: string | null; readonly kinds: ReadonlySet<EntityKind> }): void {
    for (const [id, button] of this.clusterButtons) button.setAttribute("aria-pressed", id === state.clusterId ? "true" : "false");
    for (const [kind, button] of this.kindButtons) button.setAttribute("aria-pressed", state.kinds.has(kind) ? "true" : "false");
    this.reset.hidden = state.kinds.size === 0;
  }
}

// ---------------------------------------------------------------------------------------------
// Details panel

export interface DossierHandlers {
  readonly onEntity: (id: string) => void;
  readonly onCluster: (id: string) => void;
  readonly onClose: () => void;
  readonly onRetryTimeline: () => void;
  readonly onFindPath: (fromId: string, toId: string) => void;
  readonly onClearPath: () => void;
  readonly onExportEntity: (id: string) => void;
}

export type TimelineState =
  | { readonly kind: "loading" }
  | { readonly kind: "loaded"; readonly events: ReadonlyArray<TimelineEvent> }
  | { readonly kind: "failed"; readonly failure: FailureText };

export type PathState =
  | { readonly kind: "idle" }
  | { readonly kind: "loading"; readonly targetName: string }
  | { readonly kind: "loaded"; readonly result: PathResult; readonly targetName: string }
  | { readonly kind: "failed"; readonly failure: FailureText; readonly targetId: string };

export class Dossier {
  private timelineHost: HTMLElement | null = null;
  private pathHost: HTMLElement | null = null;
  private pathClearButton: HTMLButtonElement | null = null;
  private currentEntityId: string | null = null;
  private readonly host: HTMLElement;
  private readonly atlas: Atlas;
  private readonly handlers: DossierHandlers;

  constructor(host: HTMLElement, atlas: Atlas, handlers: DossierHandlers) {
    this.host = host;
    this.atlas = atlas;
    this.handlers = handlers;
  }

  /** Nothing selected: what the map is, and how to move around it. */
  showIntro(): void {
    this.timelineHost = null;
    this.pathHost = null;
    this.pathClearButton = null;
    this.currentEntityId = null;
    clear(this.host);
    fill(this.host, 
      h("h2", { class: "title" }, "The living map of technology"),
      h("p", { class: "reading" }, "Each mark is a technology, platform or company. Lines are relationships between them. The outlined regions are clusters, found automatically in those relationships."),
      this.noticeBlock(),
      h("h3", { class: "small-heading" }, "Moving around"),
      h(
        "ul",
        { class: "hints" },
        h("li", {}, h("kbd", {}, "Ctrl K"), " or ", h("kbd", {}, "/"), " searches"),
        h("li", {}, h("kbd", {}, "← ↑ → ↓"), " move between entities"),
        h("li", {}, h("kbd", {}, "+"), " ", h("kbd", {}, "−"), " ", h("kbd", {}, "0"), " zoom and fit"),
        h("li", {}, "Select a region or a cluster in the list to see its members"),
      ),
    );
  }

  showMissing(): void {
    this.timelineHost = null;
    this.pathHost = null;
    this.pathClearButton = null;
    this.currentEntityId = null;
    const state = errorState("not_found");
    clear(this.host);
    fill(this.host, h("h2", { class: "title" }, state.title), h("p", { class: "reading" }, "The link points to an entity that is not on this map. Search for what you were looking for."), this.closeButton());
  }

  showEntity(entity: Entity): void {
    const visual = kindVisual(entity.kind);
    const connections = this.atlas.connections.get(entity.id) ?? [];
    const clusterId = this.atlas.clusterOf.get(entity.id);
    const cluster = this.atlas.clusters.find((c) => c.id === clusterId);
    const scores = this.atlas.entities.map((e) => this.atlas.importance(e.id));
    const method = this.atlas.importanceMethod === "pagerank" ? "PageRank, estimated" : "by connection count";

    clear(this.host);
    const facts = h("dl", { class: "facts" });
    if (cluster) {
      const link = h("button", { type: "button", class: "link" }, cluster.detected ? `${cluster.label} cluster` : cluster.label);
      link.addEventListener("click", () => this.handlers.onCluster(cluster.id));
      facts.append(h("dt", {}, "Cluster"), h("dd", {}, link, ` · ${plural(cluster.size, "entity", "entities")}`));
    }
    facts.append(h("dt", {}, "Centrality"), h("dd", {}, `${describeRank(this.atlas.importance(entity.id), scores)} (${method})`));

    const sameProvenance = new Set(connections.map((c) => c.relationship.provenance));
    const list = h("ul", { class: "sentences" });
    for (const connection of connections) {
      const other = this.atlas.byId.get(connection.otherId);
      if (!other) continue;
      const phrase = RELATION_PHRASES[connection.relationship.type][connection.direction];
      const parts = splitSentence(`${entity.name} ${phrase} ${other.name}.`, other.name);
      const button = h("button", { type: "button", class: "link", "data-entity": other.id }, other.name);
      button.addEventListener("click", () => this.handlers.onEntity(other.id));
      list.append(
        h(
          "li",
          {},
          parts.before,
          button,
          parts.after,
          sameProvenance.size > 1 ? h("span", { class: "row-tag" }, provenanceVisual(connection.relationship.provenance).label) : null,
        ),
      );
    }

    this.currentEntityId = entity.id;
    this.timelineHost = h("div", { class: "timeline-host" });
    this.pathHost = h("div", { class: "path-host" });
    const others = [...this.atlas.entities]
      .filter((other) => other.id !== entity.id)
      .sort((a, b) => a.name.localeCompare(b.name));
    const pathSelect = h(
      "select",
      { id: "dossier-path-target", class: "path-select", "aria-label": `Find shortest path from ${entity.name} to` },
      h("option", { value: "" }, "Choose another entity…"),
      ...others.map((other) => h("option", { value: other.id }, `${other.name} (${kindVisual(other.kind).label})`)),
    );
    const traceButton = h("button", { type: "submit", class: "path-button" }, "Trace");
    const clearPathButton = h("button", { type: "button", class: "text-button path-clear", hidden: true }, "Clear");
    clearPathButton.addEventListener("click", () => this.handlers.onClearPath());
    this.pathClearButton = clearPathButton;
    const pathForm = h("form", { class: "path-form" }, pathSelect, traceButton, clearPathButton);
    pathForm.addEventListener("submit", (event) => {
      event.preventDefault();
      if (pathSelect.value !== "") this.handlers.onFindPath(entity.id, pathSelect.value);
    });
    const exportButton = h("button", { type: "button", class: "text-button dossier-export" }, "Export entity JSON");
    exportButton.addEventListener("click", () => this.handlers.onExportEntity(entity.id));

    fill(this.host,
      h(
        "header",
        { class: "entity-head" },
        this.closeButton(),
        h("p", { class: "kicker" }, kindGlyph(entity.kind, 14), `${visual.label} · ${visual.groupLabel}`),
        h("h2", { class: "title", id: "dossier-title" }, entity.name),
        entity.aliases.length > 0 ? h("p", { class: "aliases" }, `Also known as ${entity.aliases.join(", ")}`) : null,
        h("p", { class: "provenance-line" }, provenanceBadge(entity.provenance), h("span", { class: "dust" }, PROVENANCE_HELP[entity.provenance])),
      ),
      facts,
      h(
        "section",
        { "aria-labelledby": "dossier-path" },
        h("h3", { class: "small-heading", id: "dossier-path" }, "Shortest path"),
        pathForm,
        this.pathHost,
      ),
      h(
        "section",
        { "aria-labelledby": "dossier-connections" },
        h("h3", { class: "small-heading", id: "dossier-connections" }, "Connections ", h("span", { class: "count" }, String(connections.length))),
        connections.length === 0 ? h("p", { class: "dust" }, emptyState("no_relationships").body) : list,
        connections.length > 0 && sameProvenance.size === 1
          ? h("p", { class: "dust small" }, `Every connection here is ${provenanceVisual([...sameProvenance][0] as Provenance).label.toLowerCase()} data.`)
          : null,
      ),
      h("section", { "aria-labelledby": "dossier-timeline" }, h("h3", { class: "small-heading", id: "dossier-timeline" }, "Timeline"), this.timelineHost),
      h(
        "section",
        { "aria-labelledby": "dossier-sources" },
        h("h3", { class: "small-heading", id: "dossier-sources" }, "Sources"),
        h("p", { class: "dust" }, emptyState("no_evidence", { seed: entity.provenance === "seed" }).body),
        exportButton,
      ),
    );
    this.setTimeline({ kind: "loading" });
    this.setPath({ kind: "idle" });
  }

  setPath(state: PathState): void {
    const target = this.pathHost;
    if (!target) return;
    clear(target);
    if (this.pathClearButton) this.pathClearButton.hidden = state.kind === "idle";
    if (state.kind === "idle") return;
    if (state.kind === "loading") {
      fill(target, h("p", { class: "dust", role: "status" }, `Tracing shortest path to ${state.targetName}…`));
      return;
    }
    if (state.kind === "failed") {
      const retry = h("button", { type: "button", class: "text-button" }, "Try again");
      const fromId = this.currentEntityId;
      retry.addEventListener("click", () => {
        if (fromId) this.handlers.onFindPath(fromId, state.targetId);
      });
      fill(
        target,
        h("p", { class: "dust" }, `${state.failure.title}. ${state.failure.body}`),
        state.failure.retryable ? retry : null,
      );
      return;
    }
    const { result, targetName } = state;
    if (!result.found || result.entities.length === 0) {
      fill(target, h("p", { class: "dust" }, `No path found to ${targetName} within 6 steps in the current graph.`));
      return;
    }
    const hops = result.relationships.length;
    const steps = h("ol", { class: "path-steps" });
    result.entities.forEach((node, index) => {
      const btn = h("button", { type: "button", class: "link", "data-entity": node.id }, node.name);
      btn.addEventListener("click", () => this.handlers.onEntity(node.id));
      const rel = result.relationships[index];
      const phrase = rel ? RELATION_PHRASES[rel.type][rel.source_id === node.id ? "out" : "in"] : null;
      steps.append(
        h(
          "li",
          { class: "path-step" },
          kindGlyph(node.kind, 12),
          btn,
          phrase ? h("span", { class: "path-hop" }, ` → ${phrase}`) : null,
        ),
      );
    });
    fill(
      target,
      h("p", { class: "dust small" }, `${plural(hops, "step", "steps")} from ${result.entities[0]?.name ?? ""} to ${targetName}.`),
      steps,
    );
  }

  setTimeline(state: TimelineState): void {
    const target = this.timelineHost;
    if (!target) return;
    clear(target);
    if (state.kind === "loading") {
      fill(target, h("p", { class: "dust", role: "status" }, "Loading dated events…"));
    } else if (state.kind === "failed") {
      const retry = h("button", { type: "button", class: "text-button" }, "Try again");
      retry.addEventListener("click", () => this.handlers.onRetryTimeline());
      fill(target, h("p", { class: "dust" }, `${state.failure.title}. ${state.failure.body}`, state.failure.wait ? ` ${state.failure.wait}` : ""), state.failure.retryable ? retry : null);
    } else if (state.events.length === 0) {
      fill(target, h("p", { class: "dust" }, emptyState("no_timeline").body));
    } else {
      const same = new Set(state.events.map((e) => e.provenance));
      fill(target, 
        h(
          "ul",
          { class: "events" },
          ...state.events.map((event) =>
            h(
              "li",
              {},
              h("time", { datetime: event.occurred_on }, formatEventDate(event.occurred_on, event.date_precision)),
              h("span", {}, event.title),
              same.size > 1 ? h("span", { class: "row-tag" }, provenanceVisual(event.provenance).label) : null,
            ),
          ),
        ),
        same.size === 1 ? h("p", { class: "dust small" }, `Dates are ${provenanceVisual([...same][0] as Provenance).label.toLowerCase()} data.`) : null,
      );
    }
  }

  showCluster(cluster: AtlasCluster): void {
    this.timelineHost = null;
    this.pathHost = null;
    this.pathClearButton = null;
    this.currentEntityId = null;
    const links = this.atlas.clusterLinks
      .filter((link) => link.a === cluster.id || link.b === cluster.id)
      .map((link) => ({ other: this.atlas.clusters.find((c) => c.id === (link.a === cluster.id ? link.b : link.a)), count: link.count }))
      .filter((entry): entry is { other: AtlasCluster; count: number } => entry.other !== undefined);

    const members = h("ul", { class: "rows" });
    for (const id of cluster.memberIds) {
      const entity = this.atlas.byId.get(id);
      if (!entity) continue;
      const button = h("button", { type: "button", class: "row" }, h("span", { class: "row-main" }, h("span", { class: "row-name" }, kindGlyph(entity.kind, 14), entity.name), h("span", { class: "row-tag" }, kindVisual(entity.kind).label)));
      button.addEventListener("click", () => this.handlers.onEntity(id));
      members.append(h("li", {}, button));
    }
    const linked = h("ul", { class: "rows" });
    for (const { other, count } of links) {
      const button = h("button", { type: "button", class: "row" }, h("span", { class: "row-main" }, h("span", { class: "row-name" }, other.label), h("span", { class: "row-tag" }, plural(count, "relationship", "relationships"))));
      button.addEventListener("click", () => this.handlers.onCluster(other.id));
      linked.append(h("li", {}, button));
    }

    clear(this.host);
    fill(this.host, 
      h(
        "header",
        { class: "entity-head" },
        this.closeButton(),
        h("p", { class: "kicker" }, cluster.detected ? "Cluster" : "Not in a cluster"),
        h("h2", { class: "title", id: "dossier-title" }, cluster.detected ? cluster.label : "Other entities"),
        h(
          "p",
          { class: "reading" },
          cluster.detected
            ? `${plural(cluster.size, "entity", "entities")} that are closely connected to each other. VYRON found this group by analysing the relationships and named it after its most central member, ${cluster.label}.`
            : "Entities the API did not place in any cluster.",
        ),
        cluster.detected ? h("p", { class: "provenance-line" }, lineSample(tokens.provenanceStyles.derived.dash), h("span", { class: "dust" }, "Computed from the relationships, not curated.")) : null,
      ),
      h("section", { "aria-labelledby": "dossier-members" }, h("h3", { class: "small-heading", id: "dossier-members" }, "Members ", h("span", { class: "count" }, String(cluster.memberIds.length))), members),
      links.length > 0 ? h("section", { "aria-labelledby": "dossier-linked" }, h("h3", { class: "small-heading", id: "dossier-linked" }, "Linked clusters"), linked) : null,
    );
  }

  private noticeBlock(): HTMLElement | null {
    if (!this.atlas.notice) return null;
    return h("p", { class: "notice" }, h("span", { class: "notice-mark" }, provenanceBadge("seed")), this.atlas.notice);
  }

  private closeButton(): HTMLButtonElement {
    const button = h("button", { type: "button", class: "icon-button close", "aria-label": "Close details", title: "Close details" }, controlIcon("close"));
    button.addEventListener("click", () => this.handlers.onClose());
    return button;
  }
}

// ---------------------------------------------------------------------------------------------
// Outline (the text alternative to the map)

export class OutlineView {
  private readonly buttons = new Map<string, HTMLButtonElement>();
  private readonly onEntity: (id: string) => void;

  constructor(host: HTMLElement, atlas: Atlas, onEntity: (id: string) => void) {
    this.onEntity = onEntity;
    clear(host);
    const outline = buildOutline({ entities: atlas.entities, relationships: atlas.relationships, truncated: false, data_notice: atlas.notice });
    fill(host, h("h2", { class: "title", id: "outline-title" }, "Outline"), h("p", { class: "reading" }, outline.summary));
    for (const group of outline.groups) {
      const section = h("section", { class: "outline-group", "aria-label": group.label }, h("h3", { class: "group-title" }, groupSwatch(group.group), group.label));
      for (const entry of group.entities) {
        const entity = atlas.byId.get(entry.id);
        const name = h("button", { type: "button", class: "link entity-link", "data-entity": entry.id }, entry.name);
        name.addEventListener("click", () => this.onEntity(entry.id));
        this.buttons.set(entry.id, name);
        const relations = h("ul", { class: "sentences" });
        for (const relation of entry.relations) {
          const parts = splitSentence(relation.sentence, relation.otherName);
          const other = h("button", { type: "button", class: "link", "data-entity": relation.otherId }, relation.otherName);
          other.addEventListener("click", () => this.onEntity(relation.otherId));
          relations.append(h("li", {}, parts.before, parts.name ? other : null, parts.after));
        }
        fill(section, 
          h(
            "article",
            { class: "outline-entity" },
            h("h4", {}, entity ? kindGlyph(entity.kind, 14) : null, name),
            h("p", { class: "dust small" }, `${entry.kindLabel} · ${entry.provenanceLabel}`),
            entry.relations.length === 0 ? h("p", { class: "dust small" }, emptyState("no_relationships").body) : relations,
          ),
        );
      }
      fill(host, section);
    }
  }

  setSelected(id: string | null): void {
    for (const [entityId, button] of this.buttons) button.setAttribute("aria-current", entityId === id ? "true" : "false");
  }
}

// ---------------------------------------------------------------------------------------------
// Search

export interface SearchHandlers {
  readonly search: (text: string, signal: AbortSignal) => Promise<ReadonlyArray<Entity>>;
  /** Called with the matches of the current text, or null when there is no query or it failed. */
  readonly onResults: (text: string, results: ReadonlyArray<Entity> | null) => void;
  readonly onChoose: (id: string) => void;
}

const DEBOUNCE_MS = 140;

export class SearchBox {
  private results: ReadonlyArray<Entity> = [];
  private active = -1;
  private timer = 0;
  private controller: AbortController | null = null;
  private generation = 0;
  private readonly input: HTMLInputElement;
  private readonly list: HTMLElement;
  private readonly status: HTMLElement;
  private readonly atlas: Atlas;
  private readonly handlers: SearchHandlers;

  constructor(input: HTMLInputElement, list: HTMLElement, status: HTMLElement, atlas: Atlas, handlers: SearchHandlers) {
    this.input = input;
    this.list = list;
    this.status = status;
    this.atlas = atlas;
    this.handlers = handlers;
    input.addEventListener("input", () => this.onInput());
    input.addEventListener("keydown", (event) => this.onKey(event));
    input.addEventListener("focus", () => {
      if (this.input.value.trim() !== "" && (this.results.length > 0 || this.list.childElementCount > 0)) this.open();
    });
    input.addEventListener("blur", () => this.closeSoon());
    // Choosing with the mouse must not blur the field before the click lands.
    list.addEventListener("mousedown", (event) => event.preventDefault());
  }

  setText(text: string): void {
    this.input.value = text;
    if (text.trim() === "") {
      this.reset();
    } else {
      this.run(text);
    }
  }

  /** Show text in the field without searching for it (used after an entity is chosen). */
  setValue(text: string): void {
    window.clearTimeout(this.timer);
    this.controller?.abort();
    this.generation += 1;
    this.input.value = text;
    this.results = [];
    this.active = -1;
    clear(this.list);
    this.close();
    this.status.textContent = "";
  }

  focus(): void {
    this.input.focus();
    this.input.select();
  }

  private onInput(): void {
    window.clearTimeout(this.timer);
    const text = this.input.value;
    if (text.trim() === "") {
      this.reset();
      return;
    }
    this.timer = window.setTimeout(() => this.run(text), DEBOUNCE_MS);
  }

  private reset(): void {
    window.clearTimeout(this.timer);
    this.controller?.abort();
    this.generation += 1;
    this.results = [];
    this.active = -1;
    clear(this.list);
    this.close();
    this.status.textContent = "";
    this.handlers.onResults("", null);
  }

  private run(text: string): void {
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    const generation = ++this.generation;
    this.handlers
      .search(text, controller.signal)
      .then((results) => {
        if (generation !== this.generation) return;
        this.results = results;
        this.active = results.length > 0 ? 0 : -1;
        this.render(text);
        this.handlers.onResults(text, results);
      })
      .catch((error: unknown) => {
        if (generation !== this.generation || (error as { code?: unknown } | null)?.code === "aborted") return;
        const failure = failureText(error);
        this.results = [];
        this.active = -1;
        clear(this.list);
        this.list.append(h("li", { class: "message", role: "presentation" }, h("strong", {}, failure.title), h("span", {}, ` ${failure.body}`), failure.wait ? h("span", {}, ` ${failure.wait}`) : null));
        this.input.setAttribute("aria-activedescendant", "");
        this.status.textContent = `${failure.title}. ${failure.body}`;
        this.open();
        this.handlers.onResults(text, null);
      });
  }

  private render(text: string): void {
    clear(this.list);
    if (this.results.length === 0) {
      const empty = emptyState("no_results", { query: text });
      this.list.append(h("li", { class: "message", role: "presentation" }, h("strong", {}, empty.title), h("span", {}, ` ${empty.body}`)));
      this.status.textContent = empty.title;
      this.input.setAttribute("aria-activedescendant", "");
      this.open();
      return;
    }
    this.results.forEach((entity, index) => {
      const cluster = this.atlas.clusters.find((c) => c.id === this.atlas.clusterOf.get(entity.id));
      const option = h(
        "li",
        { role: "option", id: `search-option-${index}`, "aria-selected": index === this.active ? "true" : "false", "data-entity": entity.id },
        kindGlyph(entity.kind, 14),
        h("span", { class: "option-name" }, entity.name),
        h("span", { class: "option-meta" }, `${kindVisual(entity.kind).label}${cluster?.detected ? ` · ${cluster.label} cluster` : ""}`),
      );
      option.addEventListener("click", () => this.choose(entity.id));
      option.addEventListener("pointermove", () => this.setActive(index));
      this.list.append(option);
    });
    this.status.textContent = plural(this.results.length, "result", "results");
    this.setActive(this.active);
    this.open();
  }

  private setActive(index: number): void {
    this.active = index;
    const options = [...this.list.querySelectorAll('[role="option"]')];
    options.forEach((option, i) => option.setAttribute("aria-selected", i === index ? "true" : "false"));
    this.input.setAttribute("aria-activedescendant", index >= 0 ? `search-option-${index}` : "");
  }

  private onKey(event: KeyboardEvent): void {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (this.results.length === 0) return;
      event.preventDefault();
      this.open();
      const step = event.key === "ArrowDown" ? 1 : -1;
      this.setActive((this.active + step + this.results.length) % this.results.length);
    } else if (event.key === "Enter") {
      const chosen = this.results[this.active];
      if (chosen) {
        event.preventDefault();
        this.choose(chosen.id);
      }
    } else if (event.key === "Escape") {
      if (!this.list.hidden) {
        event.preventDefault();
        event.stopPropagation();
        this.close();
      } else if (this.input.value !== "") {
        event.preventDefault();
        event.stopPropagation();
        this.input.value = "";
        this.reset();
      }
    }
  }

  private choose(id: string): void {
    this.close();
    this.handlers.onChoose(id);
  }

  private open(): void {
    this.list.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  }

  private close(): void {
    this.list.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.input.setAttribute("aria-activedescendant", "");
  }

  private closeSoon(): void {
    window.setTimeout(() => this.close(), 120);
  }
}
