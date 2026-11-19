/**
 * The page's address: the shared map state (`packages/graph/src/map-state.ts`) plus which view is open.
 * Parsing never throws; anything it cannot use is reported and dropped, and the prototype only has
 * the ecosystem mode, so any other mode is reported too rather than pretending to exist.
 */
import { MAP_DEFAULTS, parseMapState, serializeMapState } from "../../../../packages/graph/src/map-state.ts";
import type { MapState } from "../../../../packages/graph/src/map-state.ts";

export type ViewKey = "map" | "outline";

export interface PageState {
  readonly map: MapState;
  readonly view: ViewKey;
}

export interface ParsedPage {
  readonly state: PageState;
  /** One line per part of the link that was dropped. */
  readonly issues: ReadonlyArray<string>;
}

export function readPageState(search: string): ParsedPage {
  const params = new URLSearchParams(search);
  const { state: parsed, issues } = parseMapState(params);
  const problems = [...issues];
  let map = parsed;
  if (map.mode !== MAP_DEFAULTS.mode) {
    problems.push(`the ${map.mode} view is not part of the prototype`);
    map = { ...map, mode: MAP_DEFAULTS.mode };
  }
  let view: ViewKey = "map";
  const raw = params.get("view");
  if (raw === "outline") view = "outline";
  else if (raw !== null && raw !== "map") problems.push("view must be map or outline");
  return { state: { map, view }, issues: problems };
}

/** The query string for `state`, with a leading "?" or an empty string for the default page. */
export function writePageState(state: PageState): string {
  const params = new URLSearchParams(serializeMapState(state.map));
  if (state.view === "outline") params.set("view", "outline");
  const text = params.toString();
  return text === "" ? "" : `?${text}`;
}
