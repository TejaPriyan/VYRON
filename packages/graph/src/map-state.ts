/**
 * The map's shareable state, encoded in the URL.
 *
 * `parseMapState` never throws and never trusts its input: every value is checked against the API's
 * enums or a strict pattern, invalid values are dropped and reported, and unknown parameters (such
 * as `utm_source`) are ignored. `serializeMapState` is canonical, so `serialize(parse(x))` is stable
 * and two equal states always produce the same URL.
 */
import { ENTITY_KIND_VALUES, MAP_MODE_VALUES } from "../../types/src/api.ts";
import type { EntityKind, MapMode } from "../../types/src/api.ts";

export const MAP_DEFAULTS = { mode: "ecosystem", depth: 1 } as const satisfies { mode: MapMode; depth: number };
export const MAX_DEPTH = 3; // mirrors the API's graph depth limit
export const MIN_YEAR = 1900;
export const MAX_YEAR = 2200;
export const MAX_QUERY_LENGTH = 100;

export interface MapState {
  readonly mode: MapMode;
  /** Entity id (UUID, lower case) the map is centred on, or null for the whole-graph view. */
  readonly focus: string | null;
  /** Empty means all kinds. Sorted, no duplicates. */
  readonly kinds: ReadonlyArray<EntityKind>;
  readonly depth: number;
  /** Time slice for the timeline mode; null means today. */
  readonly year: number | null;
  /** Free-text search that the map is highlighting. */
  readonly query: string;
}

export interface ParseResult {
  readonly state: MapState;
  /** One line per value that was dropped, for logging and for a quiet "link was adjusted" notice. */
  readonly issues: ReadonlyArray<string>;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const INTEGER = /^-?\d{1,6}$/;
// Control characters, bidi overrides and line separators never belong in a search string.
const UNSAFE_TEXT = /[\u{0}-\u{1f}\u{7f}-\u{9f}\u{200e}\u{200f}\u{202a}-\u{202e}\u{2028}\u{2029}\u{2066}-\u{2069}]/gu;

export function emptyMapState(): MapState {
  return { mode: MAP_DEFAULTS.mode, focus: null, kinds: [], depth: MAP_DEFAULTS.depth, year: null, query: "" };
}

export function cleanQuery(raw: string): string {
  return raw.replace(UNSAFE_TEXT, " ").replace(/\s+/g, " ").trim().slice(0, MAX_QUERY_LENGTH).trim();
}

const isMode = (value: string): value is MapMode => (MAP_MODE_VALUES as ReadonlyArray<string>).includes(value);
const isKind = (value: string): value is EntityKind => (ENTITY_KIND_VALUES as ReadonlyArray<string>).includes(value);

export function parseMapState(params: URLSearchParams): ParseResult {
  const issues: string[] = [];
  let state = emptyMapState();

  const mode = params.get("mode");
  if (mode !== null) {
    if (isMode(mode)) state = { ...state, mode };
    else issues.push(`mode "${truncate(mode)}" is not a map mode`);
  }

  const focus = params.get("focus");
  if (focus !== null) {
    const id = focus.toLowerCase();
    if (UUID.test(id)) state = { ...state, focus: id };
    else issues.push("focus is not an entity id");
  }

  const kinds = new Set<EntityKind>();
  for (const value of params.getAll("kind")) {
    if (isKind(value)) kinds.add(value);
    else issues.push(`kind "${truncate(value)}" is not an entity kind`);
  }
  state = { ...state, kinds: ENTITY_KIND_VALUES.filter((kind) => kinds.has(kind)) };

  const depth = params.get("depth");
  if (depth !== null) {
    const n = INTEGER.test(depth) ? Number(depth) : Number.NaN;
    if (Number.isInteger(n) && n >= 0 && n <= MAX_DEPTH) state = { ...state, depth: n };
    else issues.push(`depth must be a whole number from 0 to ${MAX_DEPTH}`);
  }

  const year = params.get("year");
  if (year !== null) {
    const n = INTEGER.test(year) ? Number(year) : Number.NaN;
    if (Number.isInteger(n) && n >= MIN_YEAR && n <= MAX_YEAR) state = { ...state, year: n };
    else issues.push(`year must be a whole number from ${MIN_YEAR} to ${MAX_YEAR}`);
  }

  const query = params.get("q");
  if (query !== null) state = { ...state, query: cleanQuery(query) };

  return { state, issues };
}

function truncate(value: string): string {
  const clean = value.replace(UNSAFE_TEXT, "?");
  return clean.length > 24 ? `${clean.slice(0, 24)}…` : clean;
}

/** Canonical query string without the leading "?". Defaults are omitted. */
export function serializeMapState(state: MapState): string {
  const out = new URLSearchParams();
  if (state.mode !== MAP_DEFAULTS.mode) out.set("mode", state.mode);
  if (state.focus !== null) out.set("focus", state.focus);
  for (const kind of ENTITY_KIND_VALUES) {
    if (state.kinds.includes(kind)) out.append("kind", kind);
  }
  if (state.depth !== MAP_DEFAULTS.depth) out.set("depth", String(state.depth));
  if (state.year !== null) out.set("year", String(state.year));
  const query = cleanQuery(state.query);
  if (query !== "") out.set("q", query);
  return out.toString();
}

export function mapStateEquals(a: MapState, b: MapState): boolean {
  return serializeMapState(a) === serializeMapState(b);
}
