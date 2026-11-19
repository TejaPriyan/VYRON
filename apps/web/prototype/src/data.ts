/**
 * Loads the map's data through the typed API client. Nothing here is invented or cached beyond the
 * page: entities, clusters and scores come from `graph/overview` and `entities`, relationships from
 * `entities/{id}/neighbors`, and the result is checked against the counts the API reported.
 */
import { walkPages } from "../../../../packages/api-client/src/client.ts";
import type { VyronClient } from "../../../../packages/api-client/src/client.ts";
import { buildAtlas } from "../../../../packages/graph/src/atlas.ts";
import type { Atlas } from "../../../../packages/graph/src/atlas.ts";
import type { Entity, GraphOverview, Relationship, TimelineEvent } from "../../../../packages/types/src/api.ts";

/** The graph endpoints allow 60 requests a minute; stop well short of that however large the graph is. */
export const MAX_NEIGHBOURHOOD_CALLS = 20;
/** One neighbourhood response reaches three hops and holds at most 300 entities (docs/API.md). */
const NEIGHBOURHOOD = { depth: 3, max_nodes: 300 } as const;

export interface Count {
  readonly loaded: number;
  readonly expected: number;
}

export interface Completeness {
  readonly entities: Count;
  readonly relationships: Count;
  /** True only when everything the API said exists is on the map. */
  readonly complete: boolean;
}

export interface AtlasLoad {
  readonly atlas: Atlas;
  readonly overview: GraphOverview;
  readonly completeness: Completeness;
  /** API calls made, for the record. */
  readonly requests: number;
}

const withSignal = (signal: AbortSignal | undefined): { signal?: AbortSignal } => (signal ? { signal } : {});

export async function loadAtlas(client: VyronClient, signal?: AbortSignal): Promise<AtlasLoad> {
  let requests = 0;
  const overview = await client.get("graph_overview", { query: { top: 20, clusters: 20 }, ...withSignal(signal) });
  requests += 1;

  const entities: Entity[] = [];
  for await (const page of walkPages(client, "list_entities", { query: { limit: 100 } }, signal ? { signal } : {})) {
    requests += 1;
    entities.push(...page.items);
  }

  // One neighbourhood per uncovered entity: each response includes every relationship among the
  // entities it returns, so walking until every entity is covered collects the whole graph, one
  // connected component at a time. The counts below say whether that worked.
  const covered = new Set<string>();
  const relationships = new Map<string, Relationship>();
  let neighbourhoodCalls = 0;
  for (const entity of entities) {
    if (covered.has(entity.id)) continue;
    if (neighbourhoodCalls >= MAX_NEIGHBOURHOOD_CALLS) break;
    neighbourhoodCalls += 1;
    const subgraph = await client.get("get_entity_neighbors", {
      path: { entity_id: entity.id },
      query: NEIGHBOURHOOD,
      ...withSignal(signal),
    });
    requests += 1;
    covered.add(entity.id);
    for (const reached of subgraph.entities) covered.add(reached.id);
    for (const relationship of subgraph.relationships) {
      relationships.set(`${relationship.source_id}|${relationship.type}|${relationship.target_id}`, relationship);
    }
  }

  const scores = new Map(overview.top_entities.map((ranked) => [ranked.entity.id, ranked.score]));
  const atlas = buildAtlas({
    entities,
    relationships: [...relationships.values()],
    clusters: overview.clusters,
    scores,
    notice: overview.data_notice,
  });

  const completeness: Completeness = {
    entities: { loaded: atlas.entities.length, expected: overview.entity_count },
    relationships: { loaded: atlas.relationships.length, expected: overview.relationship_count },
    complete:
      atlas.entities.length === overview.entity_count &&
      atlas.relationships.length === overview.relationship_count &&
      atlas.omittedRelationships === 0,
  };
  return { atlas, overview, completeness, requests };
}

export async function loadTimeline(client: VyronClient, entityId: string, signal?: AbortSignal): Promise<ReadonlyArray<TimelineEvent>> {
  const events: TimelineEvent[] = [];
  for await (const page of walkPages(client, "get_entity_timeline", { path: { entity_id: entityId } }, { maxPages: 5, ...(signal ? { signal } : {}) })) {
    events.push(...page.items);
  }
  return events;
}

/** Pages of 100 events the time axis will read: 500 events, far past the 40 the seed holds. */
export const MAX_TIMELINE_PAGES = 5;

export interface EventsLoad {
  readonly events: ReadonlyArray<TimelineEvent>;
  /** How many events the API said there are, which is more than `events.length` only when the walk stopped early. */
  readonly expected: number;
  readonly complete: boolean;
  /** The API's own note when any of it is development seed data. */
  readonly notice: string | null;
}

/**
 * Every dated event the API has (it leaves predictions out unless asked), for the time axis. The result says whether it is complete, because an axis drawn from half the events would quietly
 * misplace the rest.
 */
export async function loadAllEvents(client: VyronClient, signal?: AbortSignal): Promise<EventsLoad> {
  const events: TimelineEvent[] = [];
  let expected = 0;
  let notice: string | null = null;
  for await (const page of walkPages(client, "timeline", { query: { limit: 100 } }, { maxPages: MAX_TIMELINE_PAGES, ...(signal ? { signal } : {}) })) {
    events.push(...page.items);
    expected = page.page.total;
    notice = notice ?? page.data_notice;
  }
  return { events, expected, complete: events.length >= expected, notice };
}

export async function searchEntities(client: VyronClient, text: string, signal?: AbortSignal): Promise<ReadonlyArray<Entity>> {
  const page = await client.get("search", { query: { q: text, limit: 8 }, ...withSignal(signal) });
  return page.items;
}
