# VYRON frontend architecture

**Status: specification plus verified pure-TypeScript modules. The Next.js application does not exist yet.**
Nothing in this document has been rendered in a browser. Packages (Next.js, React, Three.js, D3, Tailwind)
cannot be installed in the environment this was written in, so everything that needs them is marked
**UNVERIFIED** below. Section 12 lists exactly what has been run and what has not.

## 1. Design plan

The brief asks for a digital atlas and observatory, not a dashboard: dark, cinematic, technical, minimal,
data-rich, with an accent used sparingly and no glassmorphism, no glow gradients and no card grids.

### 1.1 Concept

VYRON is drawn the way a cartographer draws uncertainty. A printed map separates surveyed boundaries from
estimated ones by line style, and a reader trusts the map because the convention is visible. VYRON does the
same for provenance: **how well a fact is backed is encoded in the line and outline style of the mark itself**
(solid for sourced, dashed for derived, dotted for conjecture, dash-dot for working data), and always spelled
out in words in panels. That is the one memorable thing. Everything around it stays quiet.

### 1.2 Tokens

The single source is `apps/web/design/tokens.json`. `scripts/generate_design_tokens.py` writes
`apps/web/src/styles/tokens.css` and `packages/ui/src/tokens.ts` from it. Values below are copied from it.

| Role | Name | Value | Used for |
| --- | --- | --- | --- |
| Surface | ink | `#0a0e14` | map canvas, page background |
| Surface | basalt | `#121821` | docked panes |
| Surface | slate | `#1a222d` | inputs, popovers |
| Text | bone | `#e8e5db` | primary text (not pure white) |
| Text | ash / dust | `#aab2bd` / `#8a94a1` | secondary and muted text |
| Accent | signal | `#3fcfe8` | interaction only: focus ring, active control, link, caret |
| Line | hairline / strong / control | `#252e3b` / `#3a4657` / `#66758a` | structure / emphasis / control borders (3:1) |

Data colour is separate from the accent and has its own rules (section 1.3).

**Type.** Two web families plus a system monospace.
Newsreader (serif, open licence) carries entity names, headings, map labels and reading text, because place
names on maps are set in serif and long-form research reads better with it. Hanken Grotesk (sans, open licence)
carries the interface. Monospace is the system stack and appears only for code identifiers (repository names,
versions, hashes); figures use tabular numerals in the sans, not monospace. Roles: title 34/1.1, heading 22/1.2,
reading 17/1.6 at most 68ch, body 15/1.5, label 13/1.3, map label 13/1.1. Nothing is smaller than 13px.

**Shape and space.** Docked panes have no radius and a 1px hairline; controls have a 3px radius; marks 2px.
No shadows. A 4px base grid. Panes dock to the viewport edges like instrument panels rather than float as cards.

**Voice.** Sentence case everywhere. No all-caps labels, no eyebrows above headings. Errors say what happened
and what to do, never apologise (`packages/ui/src/states.ts`, tested). Actions keep one name end to end.

### 1.3 Colour encodes four groups, shape encodes the rest

The reference eight-hue categorical palette passes the colour-vision gates only for neighbouring pairs. A node-link
graph compares every pair, and with all 28 pairs in play the eight hues fail both gates (worst colour-blind
separation 1.6, worst normal-vision separation 7.1; the floors are 6 and 15). A brute-force search of every
subset found that four hues is the most that clear all pairs. So:

| Group | Colour | Entity kinds |
| --- | --- | --- |
| Software and infrastructure | `#9085e9` | technology, language, framework, library, platform, database, repository, api |
| AI and research | `#c98500` | ai_model, dataset, research_paper |
| Organizations | `#d55181` | company, organization |
| Events | `#008300` | event |

Within a group, **shape** tells kinds apart (circle, square, diamond, triangle, inverted triangle, hexagon, plus,
four-point star); the pair (group, shape) is unique for all 14 kinds. The worst colour-blind pair of the four is
6.9, inside the 6 to 8 band that is legal only with a second encoding, which shape provides. The exact kind is
always available as text (label, panel, outline). Colour follows the entity, never its rank: a filter that
removes a group does not repaint the rest.

`tests/test_design_contract.py` computes all of this: WCAG contrast of every text, accent, status, control and
mark colour on all three surfaces; lightness band and chroma floor; all-pairs normal and colour-blind separation;
that the accent cannot be confused with a data hue; that every API entity kind and provenance label has an
encoding; and that the generated CSS and TypeScript match the JSON. The colour maths is a standard-library
re-implementation pinned to the numbers produced by a separate validator.

Status colours (ok, warning, error) are for text with an icon and a word, never on the canvas.
AI-generated content is marked by an icon and the words "AI-generated", not by a hue, so no hue means "AI".

### 1.4 Layout

Desktop at 1440px and wider. The canvas is the page; chrome recedes.

```
+--------------------------------------------------------------------------+
| VYRON   [ Search technologies, companies, repositories     Ctrl K ]  Ecosystem |  48px
+-----------+----------------------------------------+---------------------+
| Map key   |                                        | Python              |
| (filters) |                                        | Language - Sourced  |
|           |        canvas: marks, edges            |                     |
| Software  |                                        | Connections (12)    |
| AI        |                                        | Evidence (3)        |
| Orgs      |                                        | Timeline            |
| Events    |                                        |                     |
+-----------+----------------------------------------+---------------------+
| 1950 ----------------------o------------- 2026      scale     seed data notice |  88px
+--------------------------------------------------------------------------+
  264px            fluid (min 700px)                    400px
```

The map key is the legend and the filter in one control: each row shows the group colour, the shapes it
contains, a count, and toggles visibility. Below 1440px the left pane collapses to a 56px icon rail; below
1100px the entity pane becomes an overlay; below 768px it becomes a bottom sheet (section 8).

Reading pages (entity, research report) are left-aligned in a single 68ch column beside a provenance ledger,
not centred and not a grid of cards. Search results and lists are ledger rows with a kind mark, name, one line
of context and the provenance label, separated by hairlines.

### 1.5 Review against the brief

The first draft of this plan was reviewed before any code and changed in these ways:

- It used the eight-hue palette. The validator rejected it for a graph, so it became four groups plus shape.
- It put the small data labels in monospace, a common default for "technical" interfaces. Replaced by tabular
  numerals in the sans; monospace survives only where the content really is code.
- The AI layer was going to be marked in the accent hue. Replaced by icon plus words, because a hue would collide
  with the data palette and colour alone is not an encoding.
- A card grid for search results and entity pages. Replaced by ledger rows and docked panes.
- Two accents (cyan and violet). Cut to one: signal cyan for interaction only. Violet remains a data hue.
- The home page opened with a headline over a decorative background. Replaced by the live map with a search
  field, because the most characteristic thing in this product is the map itself.

What still reads as a default and is accepted on purpose: a near-black background with one bright accent. The
brief names both. The accent is a cool cyan, not the acid green or vermilion that dominate that pattern, it never
appears on data, and it is outnumbered by the provenance line styles that carry the product's identity.

### 1.6 Motion

Motion shows what changed, never decorates. The camera eases to a selection (600ms), a path draws along its hops
in order, a neighbourhood fades in from its parent. Nothing loops and nothing animates on page load except the
first camera settle. With `prefers-reduced-motion`, every duration token becomes 0ms (`tokens.css` does this),
the camera jumps and a path appears whole.

## 2. Principles

1. The graph works without AI, and the interface never implies otherwise. AI surfaces are labelled and removable.
2. Every statement shows how it is backed. Seed data carries a persistent "Development seed data" notice.
3. No invented numbers. A metric with no defined calculation is not shown; a computed one links to its method
   (the overview ranking is labelled `estimated`, method `pagerank`, by the API itself).
4. A visual is never the only way in. Everything on the map is reachable as text and by keyboard.
5. The browser never receives a secret and never talks to a provider; it talks to this application only.

## 3. Application architecture (UNVERIFIED: Next.js not installed)

Next.js App Router, TypeScript strict, Tailwind reading the generated CSS variables.

```
 browser ──► Next.js server (BFF) ──► FastAPI /api/v1 ──► PostgreSQL / Neo4j / Redis
              server components            (verified contract)
              route handlers /api/*        Gemini and GitHub only here, server-side
```

- **Server-only data access.** Server components and route handlers call the API through
  `packages/api-client`. The API base URL and any service credentials are environment variables read on the
  server. Client components call same-origin route handlers, which forward the session cookie and the client
  address headers and strip everything else. No `NEXT_PUBLIC_` variable carries a secret.
- **Types from the contract.** `packages/types/src/api.ts` is generated from `docs/openapi.json`. A renamed
  parameter or response field is a compile error (checked by `packages/api-client/src/typecheck.fixture.ts`).
- **Rendering per route.** Content pages are server-rendered for fast first paint and shareable previews; the
  map and any pointer-heavy view is a client component loaded lazily.

| Route | Rendering | API operations | State today |
| --- | --- | --- | --- |
| `/` | server shell, client map | `graph_overview`, `get_entity_neighbors`, `search` | implemented |
| `/explore` | server, client filters | `search`, `list_entities`, `list_technologies`, `list_companies` | implemented |
| `/map` | client (WebGL) | `graph_overview`, `get_entity_neighbors`, `graph_path`, `timeline` | implemented |
| `/entity/[id]` | server, revalidated by ETag | `get_entity`, `get_entity_neighbors`, `get_entity_timeline`, `get_entity_evidence` | evidence is planned |
| `/timeline` | server shell, client scrubber | `timeline` | implemented |
| `/search` | server | `search` | implemented |
| `/github` | client form | `github_parse`, `get_github_rate_limit` | parse implemented; rate limit planned |
| `/repository/[owner]/[repo]` | server | `get_repository_analysis` | planned |
| `/research` | client, polls a run | `create_research_run`, `get_research_run` | planned |
| `/future` | client | `create_future_scenario` | planned |
| `/genome`, `/archaeology` | client | no endpoint exists yet | design only |

A route whose operation is **planned** renders the "Not available yet" state (`not_implemented` in
`states.ts`), driven by the real 501 from the API. It never shows placeholder content.

### 3.1 State

- **URL state** is the source of truth for anything shareable: mode, focused entity, kind filters, depth, year,
  search text. `packages/graph/src/map-state.ts` parses it defensively (invalid values are dropped and reported,
  unknown parameters ignored) and serialises canonically, so equal states give identical URLs.
- **Server state** (entities, neighbourhoods) lives in a request cache keyed by operation and parameters;
  revalidation uses the API's ETag and `Cache-Control`. TanStack Query is the intended library; UNVERIFIED.
- **Ephemeral UI state** (hover, camera, open panes) lives in component state or a small store. It is never put
  in browser storage as a source of truth; storage is for per-viewer conveniences only and every read is guarded.

### 3.2 Errors and loading

Every failure is an `ApiRequestError` with a stable `code`; `packages/ui/src/states.ts` maps each code to a title,
a body and one action (retry, sign in, reload, report). Rate limiting shows the wait from `Retry-After`. Loading
states keep layout stable (reserve the space) and announce through a polite live region. An aborted request shows
nothing.

## 4. Graph rendering (UNVERIFIED: no renderer written)

**Never one DOM node per entity.** The API returns at most 300 entities and 1,000 relationships per request
(`MAX_GRAPH_NODES`, `MAX_GRAPH_EDGES`) and says when it truncated; the client holds the union of what the reader has
opened.

- **Renderer interface.** `GraphRenderer` (set data, set view, pick at point, highlight ids, resize, dispose) with
  two implementations behind it: WebGL via Three.js instanced meshes for the main map, and an SVG renderer used for
  small neighbourhoods on entity pages and as the fallback when WebGL is unavailable or reduced motion and low
  power are requested. The outline (section 6) is a third, non-visual view of the same data.
- **Layout.** Force simulation in a Web Worker so the main thread keeps its frame budget. Starting positions come
  from `seedPosition(id)` (`layout-seed.ts`): a pure function of the entity id, so reopening a map or loading a
  second neighbourhood does not scramble what the reader already knows.
- **Level of detail** (`lod.ts`, tested). Zoom is a scale where 1 fits the graph. Below 1.25 the map shows cluster
  summaries from `graph_overview`; from 1.25 individual entities; from 2.5 every label that fits. Labels are
  chosen greedily by importance with no overlaps, pinned ids (selected, hovered, search hit) first, within a budget
  that scales with canvas area. Edges outside the viewport are culled.
- **Progressive loading.** Open on the overview (clusters and top entities), expand a cluster or a selection by
  calling `get_entity_neighbors` with depth 1 to 3, merge by id. Never request the whole graph.
- **Picking.** With at most a few thousand marks, a spatial grid built from the same positions as the draw call; a
  GPU id buffer only if measurement shows it is needed.
- **Interaction.** Click or Enter selects and opens the entity pane. Selecting a relationship opens its explanation
  with provenance and, once the endpoint exists, evidence. Edge highlighting dims everything outside the selected
  neighbourhood by opacity; it does not recolour.
- **Provenance on the canvas.** Edges use the dash pattern of their provenance style; node outlines use the matching
  outline style; the map key explains both.
- **Digital City** is a rendering mode over this same graph (height from a stated, defined metric, district from
  cluster). It has no data model of its own and does not ship until a metric with a real calculation exists.
- **Budgets** (targets, unmeasured): 16.6ms frame at the API's maximum payload, shell JS 180kB gzip, map chunk
  350kB gzip, LCP 2.5s, INP 200ms, CLS 0.1. They live in `tokens.json` under `budget`, and
  `tests/test_design_contract.py` ties the payload figures to the API's own limits.

## 5. Keyboard model for the map

The canvas container is one tab stop with an accessible name that summarises the view, and its instructions are
associated by `aria-describedby`. Inside it:

| Key | Action |
| --- | --- |
| Arrow keys | Move selection to the nearest entity in that direction |
| `[` and `]` | Previous and next relationship of the selection |
| Enter | Open the entity pane for the selection |
| `+`, `-`, `0` | Zoom in, zoom out, fit to view |
| Escape | Clear selection, close the topmost layer |
| `/` | Focus search |
| Ctrl or Cmd K | Command palette |

The same actions are available as visible buttons in the graph controls, so no function depends on a gesture or a
shortcut. Pointer gestures (pinch, drag, wheel) always have a button equivalent. Targets are 32px with a fine
pointer and 44px with a coarse one.

## 6. Accessibility

Target: WCAG 2.2 AA. This is a design commitment, not a claim about a built interface.

- **Non-visual alternative to the map.** `buildOutline` (`packages/graph/src/outline.ts`, tested, including against
  the real API) turns the visible subgraph into text: groups in legend order, entities sorted by name, and every
  relationship as a sentence from both ends ("Python is used by NumPy."), each with its provenance in words. A
  `RELATION_PHRASES` table is total over the API's relation types, so a new type cannot ship without words. The
  outline is a real DOM list, reachable from a skip link and a toggle that appears before the canvas.
- **Colour is never alone.** Group colour plus shape plus text; provenance by line style plus icon plus words;
  status by icon plus word. Contrast of every token pair is computed in tests.
- **Focus.** A visible 2px ring with a 2px gap on every focusable element; on the canvas, a bone-and-ink double
  ring that reads over any mark. Focus never moves on its own except into a dialog, back to its trigger on close,
  and to the entity pane heading after selection with Enter.
- **Live regions.** Selection and load results are announced politely (`describeSelection`); validation and server
  faults assertively (`urgent` in `states.ts`).
- **Reduced motion, forced colors, contrast preference.** Durations collapse to zero. In `forced-colors: active`,
  marks use system colours and keep their dash patterns and shapes. With `prefers-contrast: more`, structural lines
  use the control-border token.
- **Dialogs and palette** trap focus, restore it, close on Escape, and are labelled. Tooltips are never the only
  carrier of information and are dismissible and hoverable.
- **Language and structure.** Semantic landmarks, one `h1` per page, headings in order, lists as lists, tables with
  headers.

Planned automated checks (UNVERIFIED, need packages): axe in component and end-to-end tests, keyboard-only
end-to-end runs, and a manual screen-reader pass before any release.

## 7. Security

- Secrets are server-side environment variables. The browser bundle is checked for the absence of known secret
  names in CI (planned).
- A strict Content-Security-Policy with per-request nonces, `frame-ancestors 'none'`, `Referrer-Policy`,
  `Permissions-Policy`, and `X-Content-Type-Options`, set by the Next.js server; the API already sets its own.
- Untrusted text (entity names, AI output, README excerpts) is rendered as text. No `dangerouslySetInnerHTML`.
  Markdown from AI or GitHub passes through an allow-list renderer that drops raw HTML and non-http(s) links, and
  external links use `rel="noopener noreferrer"`.
- Every URL parameter is validated before use (`map-state.ts`). The API client refuses protocol-relative and
  non-http(s) base URLs, because a browser would send requests to another origin (found and fixed by its tests).
- Session cookie is `HttpOnly`, `Secure`, `SameSite=Lax`; state-changing route handlers verify `Origin` and a CSRF
  token. Auth is planned in the API, so none of this is exercised yet.

## 8. Responsive layouts

Mobile is designed, not shrunk. Below 768px:

```
+------------------------+    +------------------------+
| VYRON        [S] [..]  |    | VYRON        [S] [..]  |
| Ecosystem  Deps  AI >  |    | Ecosystem  Deps  AI >  |
+------------------------+    +------------------------+
|                        |    |                        |
|        canvas          |    |        canvas          |
|                        |    +------------------------+
|                        |    | Python                 |
| [-] [+] [fit] [list]   |    | Language - Sourced     |
+------------------------+    | Connections   Evidence |
| Map  Explore  Time  You|    | (sheet, 3 snap heights)|
+------------------------+    +------------------------+
  browsing                       entity selected
```

- Navigation is a bottom tab bar; modes are a horizontally scrolling segmented control; search opens full screen.
- The entity pane is a bottom sheet with three snap heights (peek, half, full), draggable and with button
  equivalents; the canvas stays interactive above it.
- Graph controls are 44px, bottom-aligned within thumb reach, and include a "list" button that switches to the
  outline.
- Filters and the map key live in a sheet, not a rail.
- Research results and the timeline are single columns; the timeline scrubber becomes a year stepper with a
  draggable track.
- Touch targets are at least 44px; hover-only information has a tap equivalent.

Between 768 and 1099px the entity pane is an overlay on the right at 400px; from 1100px it docks, with the map key
collapsed to the 56px rail until 1440px.

## 9. Command palette

Ctrl or Cmd K. Commands: search technology, open map, search GitHub, open timeline, start research, open Future Lab,
open Digital Archaeology, open Software Genome, toggle Digital City, open settings. A command whose route is
planned is listed with "Not available yet" and is still focusable, so its absence is explained rather than hidden.
Search results in the palette come from `search`.

## 10. Testing strategy

Verified now (standard library, Node's built-in runner, and the preinstalled TypeScript compiler):

- the colour and token contract (`tests/test_design_contract.py`);
- generated TypeScript types current, generator refusals, dependency-free guarantee, `tsc --strict` clean
  (`tests/test_typescript_contract.py`);
- TypeScript unit suites run by `node --test` (client, map geometry, level of detail and label placement, outline,
  states, and the prototype's page logic);
- the TypeScript client and the prototype's data loading against the real Python API over a local socket, started
  and stopped by `tests/test_ts_integration.py`;
- the prototype's server and page contract (`tests/test_prototype.py`);
- one scripted Chromium run of the prototype (`scripts/verify_prototype_browser.py`), run by hand because it needs
  the Python `playwright` package and a Chromium build. See [PROTOTYPE.md](PROTOTYPE.md).

Planned and **UNVERIFIED** (need packages): component tests with Vitest and Testing Library; Playwright end-to-end
runs of the Next.js app in CI; axe checks; visual regression on the map with deterministic seeds; bundle-size and
Web Vitals measurement against the budgets; runs in Firefox and Safari.

## 11. Packages

| Package | Contents | Verified |
| --- | --- | --- |
| `packages/types` | generated API types and route table | `tsc --strict`, freshness test |
| `packages/api-client` | typed fetch client, error model, page walker | unit tests, integration tests against the real API, compile-time assertions |
| `packages/graph` | atlas indexes, seeded force layout, camera, glyph shapes, cluster hulls, level of detail, label and relationship-phrase placement, keyboard navigation, outline, map URL state | unit tests; outline and layout also checked against the real API; used by the prototype |
| `packages/ui` | generated tokens, error and empty-state copy | unit tests plus the token contract |
| `apps/web` | design tokens source, generated CSS | token contract; the Next.js app is not written |
| `apps/web/prototype` | the dependency-free browser prototype of the map ([PROTOTYPE.md](PROTOTYPE.md)): static page, `tsc`-compiled modules, one-origin development server | unit, data, server and page tests; 67 Chromium checks. It is a review aid, not the app described in this document |

Dependency rule: verified packages import only relative files (enforced by a test). When packages can be installed,
relative cross-package imports become workspace imports and Next.js transpiles them.

## 12. What is and is not verified

| Item | Status |
| --- | --- |
| Token contrast, colour-blind separation, coverage of API enums | **Verified** by computation (not by looking at a screen) |
| Generated TypeScript types and strict type check | **Verified** |
| API client against the real API | **Verified** over localhost on seed data |
| Map geometry (layout, camera, hulls, level of detail, label placement, outline, URL state), error copy | **Verified** by unit tests |
| The prototype's SVG map at 60 entities: appearance, responsive layout (desktop, medium, phone), search, selection, keyboard and touch use, outline, API-failure and bad-address states | **Verified in Chromium only** (462 checks across the map, repository and dependencies pages and 48 screenshots, 2026-11-19) |
| The prototype's dependencies page (a package's declared dependencies from PyPI or npm, one package per request, graph grown on demand, text outline, and an opt-in release history of the dates each registry records) | **Verified in Chromium only, against a local stand-in of fixture data**; **not seen showing a real PyPI or npm answer** (the dependencies route was reported passing against both on 2026-11-15; the release dates have never been read from either), **UNVERIFIED** in forced-colours mode and with a screen reader ([PROTOTYPE.md](PROTOTYPE.md), [REGISTRIES.md](REGISTRIES.md)) |
| Next.js app, React components, routing, server rendering, BFF | **UNVERIFIED**, not written |
| WebGL renderer, force layout worker, picking, camera at scale | **UNVERIFIED**, not written (the prototype's SVG renderer and main-thread layout were seen only at 60 entities) |
| Fonts (Newsreader, Hanken Grotesk): loading, rendering, and the OFL licence field (recorded from memory; confirm when the files are fetched) | **UNVERIFIED**; the prototype screenshots show fallback fonts |
| Appearance and responsive behaviour in any browser except Chromium, and on real devices; the timeline strip's look beyond the prototype's Chromium run (its year slider and "map as of a year" are in the prototype, on seed dates, see [PROTOTYPE.md](PROTOTYPE.md)), other map modes and their animation | **UNVERIFIED** |
| Accessibility beyond computed contrast, the text outline and DOM-level checks of the prototype (roles, labels, live regions, focus order) | **UNVERIFIED**: no screen reader or accessibility checker has been run |
| Performance budgets | **UNVERIFIED**, targets only |
