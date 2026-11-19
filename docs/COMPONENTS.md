# VYRON component specifications

**Status: specification only. No React component exists yet (UNVERIFIED).** What is already built and tested is
what the components will render: tokens (`packages/ui/src/tokens.ts`), error and empty-state copy
(`packages/ui/src/states.ts`), the outline view-model (`packages/graph/src/outline.ts`), map URL state, and label
placement. Prop shapes below are design notes written in TypeScript syntax; they are not compiled.

A dependency-free **browser prototype** ([PROTOTYPE.md](PROTOTYPE.md)) implements part of the map, key, details,
outline and search specifications below in plain TypeScript and SVG. It is not these React components and does not
claim to satisfy their specifications; where it differs, the differences are listed under MapCanvas.

Design language, tokens and the reasoning behind them are in [FRONTEND.md](FRONTEND.md). Token names in this
document are the generated CSS custom properties without the `--vy-` prefix.

## 0. Rules that apply to every component

1. **Tokens only.** No literal colour, size, radius or duration. Anything missing is added to
   `apps/web/design/tokens.json` and regenerated.
2. **Structural lines are never the only boundary of an interactive control.** `line-hairline` and `line-strong`
   are structure (panes, rows); they fall below 3:1 on every surface on purpose. A control's own edge, an input's
   border or a checkbox box uses `line-control` (at least 3:1, computed in tests) or a filled surface.
3. **Colour is never the only carrier.** Group colour comes with a shape and a text label; provenance comes with a
   line style, an icon and words; status comes with an icon and a word.
4. **Focus is always visible.** 2px `signal` ring with a 2px `ink` gap (`border-focus`, `border-focus-gap`). On the
   canvas the ring is bone with an ink gap so it reads over any mark.
5. **Targets.** `target-fine-pointer` (32px) and `target-coarse-pointer` (44px, applied automatically). Never below
   `target-minimum` (24px) even in dense tables.
6. **Motion** uses `motion-*` and `ease-*` tokens only and inherits the reduced-motion override. A component never
   reads `matchMedia` itself for this.
7. **Text.** Sentence case. Labels name what a control does or what a field holds, in plain words. No all-caps, no
   trailing arrows, no exclamation marks, no apology. One name per action from button to confirmation.
8. **Untrusted text is text.** Entity names, AI output and README excerpts render as text nodes. Rich content passes
   through the allow-list markdown renderer (no raw HTML, http(s) links only, `rel="noopener noreferrer"`).
9. **Every component renders its loading, empty and error states** from `states.ts`, or says in its spec why it has
   none.
10. **Seed data announces itself.** Any surface showing an entity or relationship with `provenance: "seed"`, or any
    response with a `data_notice`, shows the notice (see SeedNotice).

## 1. Primitives

### Button

- **Variants.** `primary` (filled `signal`, text `signal-ink`, 8.9:1), `secondary` (`line-control` border, bone
  text), `quiet` (no border, ash text, used inside panes), `danger` (status-error text with icon, confirmation
  required). One `primary` per view.
- **Props.** `variant`, `size` (`default` | `compact`), `icon?`, `loading?`, `disabled?`, `type`, `onPress`.
- **States.** rest, hover (`signal-hover` on primary; `slate` fill on others), pressed, focus-visible, disabled
  (`dust` text, no pointer events, still focusable only when it explains why via `aria-describedby`), loading
  (spinner replaced by a determinate label such as "Saving", `aria-busy`, width does not change).
- **Behaviour.** Native `<button>`. Icon-only buttons require an accessible name and a tooltip.
- **Copy.** Verb first: "Save map", "Clear filters". Never "Submit", "OK" or "Click here".

### TextField and SearchField

- **Anatomy.** Visible label above (never placeholder-only), control on `slate` with a `line-control` border and
  3px radius, optional hint below, error below replacing the hint.
- **SearchField** adds a leading magnifier, a clear button once non-empty, a shortcut hint ("Ctrl K" on desktop only)
  and an attached results listbox.
- **Props.** `label`, `value`, `onChange`, `hint?`, `error?`, `required?`, `maxLength?` (always set; the API bounds
  search at 100 characters), `inputMode?`, `autoComplete`.
- **A11y.** `aria-invalid` and `aria-describedby` for hint and error; error text starts with what to change.
  SearchField follows the combobox pattern: `role="combobox"`, `aria-expanded`, `aria-controls`,
  `aria-activedescendant`; arrow keys move, Enter chooses, Escape closes then clears.
- **States.** rest, hover, focus, filled, disabled, read-only, error, loading results ("Searching").
- **Data.** Search calls `search` with a 150ms debounce, cancels the previous request through `AbortSignal`, and
  shows `no_results` from `states.ts` when the response is empty.

### Panel (docked pane)

- **Purpose.** The only container. Docks to a viewport edge, full height of its region, `basalt` background, a
  single `line-hairline` edge on the side facing the canvas, no radius, no shadow.
- **Anatomy.** Header (title in `heading`, close or collapse control), scroll body, optional footer.
- **Variants.** `rail` (left, 264px or 56px collapsed), `dossier` (right, 400px), `overlay` (between 768 and 1099px,
  above the canvas with `layer-pane`), `sheet` (below 768px, see Sheet).
- **A11y.** `role="region"` with `aria-labelledby` the title. Collapsing a pane keeps its toggle in the tab order.
- **Does not** nest panels or card-inside-card.

### Entry (ledger row)

Replaces the "card". One entity, relationship, event or search result as a row.

- **Anatomy.** KindMark, name in `heading`-scale serif at body size, one line of context in `ash`, ProvenanceMark at
  the end, hairline below.
- **Props.** `entity` (the API `Entity`), `context?`, `selected?`, `href` or `onSelect`.
- **States.** rest, hover (`slate` fill), selected (2px `signal` bar on the leading edge and `aria-current`), focus.
- **A11y.** The row is one link or button; its name is "Name, kind, provenance". Long names wrap, never truncate
  without a title.

### Badges

- **KindMark.** The shape for the entity's kind drawn at 12 or 16px in its group colour, followed by the kind label
  in text. The shape glyph set is defined once (circle, square, diamond, triangle, inverted triangle, hexagon, plus,
  star) and reused by the canvas, the map key and this mark so they cannot drift. Decorative when the label is
  adjacent (`aria-hidden`).
- **ProvenanceMark.** Icon plus the label from `provenanceVisual` ("Sourced", "Estimated", "Inferred", "Predicted",
  "AI-generated", "Your note", "Development seed"), in `ash` text, with the style's dash pattern on its underline.
  Never coloured by hue. A tooltip explains what the label means and what backs it.
- **StatusBadge.** ok, warning or error colour with an icon and a word, for job and rate-limit state only.

### Tabs

- Pattern: `role="tablist"`; automatic activation for cheap panes, manual activation when a tab triggers a request.
- Active tab: `bone` text with a 2px `signal` underline. Inactive: `ash`.
- Arrow keys move, Home and End jump, Tab leaves the list. Panels are `role="tabpanel"` labelled by their tab.
- Overflow scrolls horizontally with visible edge fade only on pointer devices; on touch, the next tab is partly
  visible to signal more.
- Used for the dossier (Overview, Connections, Evidence, Timeline) and map modes.

### Tooltip

- Appears on hover and on focus, after 400ms on hover and immediately on focus. Dismisses on Escape and when the
  pointer leaves both trigger and tooltip (so it stays hoverable).
- Holds supplementary text only. Anything a person needs (a provenance meaning, a metric's method) is also reachable
  without hover.
- `role="tooltip"` and `aria-describedby`. Max width 280px, `slate` surface, `line-control` border.
- Touch: first tap opens it, second activates the control.

### Dialog and Sheet

- **Dialog.** Modal, `role="dialog"` with `aria-modal`, labelled by its title, focus moves to the first field or the
  title, is trapped, and returns to the trigger on close. Escape and the close button both close. Scrim is `ink` at
  60%. Used for confirmations, save-map and export.
- **Sheet.** The mobile form of the dossier and filters. Three snap heights (peek, half, full), draggable by its
  handle, with Expand and Collapse buttons as equivalents. Non-modal at peek and half (the canvas stays operable);
  modal at full.
- Both respect `layer-sheet` and `layer-dialog`; neither is used for content that should be a page.

### CommandPalette

- Ctrl or Cmd K opens a dialog containing a SearchField and a grouped listbox.
- **Commands.** Search technology, Open map, Search GitHub, Open timeline, Start research, Open Future Lab, Open
  Digital Archaeology, Open Software Genome, Toggle Digital City, Open settings. Entity results from `search` appear
  under "Entities".
- A command whose route is **planned** shows "Not available yet" beside it and is still selectable; choosing it opens
  the real page, which explains. It is never hidden.
- Keyboard: type to filter, arrows move, Enter runs, Escape closes, Tab cycles groups. Shortcuts display next to
  commands.
- Result count announced politely: "6 results".

### Navigation

- **TopBar** (48px, desktop and tablet). Wordmark, SearchField trigger, map mode selector, settings. The current page
  has `aria-current="page"`.
- **TabBar** (below 768px). Map, Explore, Timeline, Account. Icons with visible text labels. Safe-area aware.
- **Skip links.** "Skip to map", "Skip to entity list" are the first focusable elements.
- **Mode selector.** Ecosystem, Dependencies, Companies, AI, Open source, Research, Timeline (from `MAP_MODE_VALUES`);
  state lives in the URL (`mode`).

### DataTable

- Real `<table>` with `<th scope>`, sortable columns exposed through `aria-sort` and a button inside the header.
- Numeric columns right-aligned with tabular numerals; text left-aligned; header row sticky.
- Row actions in the last column, 32px targets.
- Pagination is cursor-based: "Next" is enabled only when `page.next_cursor` is non-null, and the total from
  `page.total` is shown ("Showing 25 of 112").
- Empty and error states from `states.ts`; loading keeps the row count stable with skeleton rows of the same height.
- Below 768px, rows become stacked definition lists, not a horizontally scrolling table.

## 2. Graph components

### MapCanvas (UNVERIFIED: React component and WebGL renderer not written)

A prototype of this component exists as `MapView` in `apps/web/prototype/src/map.ts` (SVG, no React, seen in
Chromium at 60 entities only). Differences from the specification below: it has no `GraphRenderer` interface and no
WebGL path, so there is no "simplified map" fallback; names are placed by `placeLabels` (four sides, collision
tested, bounded to the visible area), which supersedes the single-side `selectLabels` written here; incomplete or
partly omitted data is reported as a notice in the page footer rather than as the `truncated` state; and the "List
view" toggle is a view switch in the top bar that shows the outline.

- Renders marks, edges and labels through the `GraphRenderer` interface (FRONTEND.md section 4).
- **Marks.** Shape and group colour from the kind table; outline style from provenance; radius from importance
  within a fixed range (never from a number that has no defined meaning).
- **Edges.** Dash pattern from provenance; `ash` at 40% opacity at rest; selected neighbourhood at full opacity and
  everything else dimmed. Direction shown by a small chevron at the target end once zoom is at node level.
- **Labels.** Chosen by `selectLabels`; serif at `type-map-label`, drawn with an `ink` halo for legibility.
- **LOD.** `detailLevel(zoom)`: clusters, nodes, labelled.
- **A11y.** The container has an accessible name from `buildOutline(...).summary`, `aria-describedby` instructions,
  the keyboard model of FRONTEND.md section 5, and a visible "List view" toggle that mounts GraphOutline.
- **States.** loading (skeleton ring of the seed positions), empty (`empty_graph`), truncated ("Showing 300 of more;
  zoom in or filter" from the response's `truncated`), error, WebGL unavailable (falls back to the SVG renderer
  silently, with the notice "Using the simplified map").

### MapKey

- The legend and filter in one. A row per group: group colour, the shapes it contains, name, count, visibility
  toggle (`aria-pressed`). Expanding a group lists its kinds with their shapes and counts.
- A second section explains the line styles (solid, dashed, dotted, dash-dot) with their words.
- Toggling updates `kind` in the URL and never recolours remaining marks.
- Counts come from the loaded subgraph; the section says so ("In this view").

### GraphControls

- Buttons: Zoom in, Zoom out, Fit to view, Reset layout, List view, Depth (0 to 3 stepper), Mode menu on small
  screens.
- All 44px on coarse pointers, grouped in one toolbar (`role="toolbar"`, arrow-key navigation between buttons).
- Every control has a text tooltip with its keyboard shortcut.

### GraphOutline

- Renders `buildOutline` as nested lists: group heading (`h3`), entities as a list, each with its relationships as
  sentences. Selecting an entity here selects it on the canvas and vice versa ("selection follows focus").
- The summary sentence is the section's accessible description. Seed notice and truncation notice are included in it.
- Works with no canvas at all and is the default view when WebGL is unavailable or the person prefers it.

### RelationshipExplanation

- Opens when a relationship is selected. Shows the sentence ("Python is used by NumPy."), its type in plain words,
  ProvenanceMark, and the Evidence list for it.
- When `get_entity_evidence` is not available (planned today), it shows `no_evidence` with the reason. It never
  fabricates a source.

## 3. Page-level components

### Dossier

- The entity pane: title (serif `title`), KindMark, ProvenanceMark, aliases, then tabs: **Overview** (facts that have
  a source, each with provenance), **Connections** (grouped by relation type, as Entries), **Evidence**,
  **Timeline** (events from `get_entity_timeline`).
- A fact with no recorded source is shown, if at all, with its real label (`inferred`, `estimated`, `seed`), never
  as plain text.
- Actions: Focus on map, Copy link, Bookmark (signed-in only, planned), Open repository (for repositories).
- Metrics: only those with a defined calculation, each with a "How this is calculated" disclosure. The overview
  ranking is labelled "Estimated: PageRank over the loaded graph".

### EvidenceList (planned data)

- One item per source: title, publisher, type, reliability label, date, locator, link out. Sorted by reliability,
  then date. A claim's confidence, when present, is a number with its meaning stated, never a bare percentage.
- Contradicting evidence is listed with the same weight as supporting evidence.

### TimelineScrubber

- Track from the first to the last year in the data, with event ticks (not a gradient). The handle moves with
  arrow keys (1 year), Page keys (10 years), Home and End. `role="slider"` with `aria-valuetext` ("2012, 14 events").
- Predicted events (`is_prediction`) sit after a labelled "Predicted from here" divider and use the conjecture
  style. Predictions are only shown when the person opts in.
- Below 768px, a stepper ("Earlier", "Later") with a draggable track.

### ResearchResult (planned)

- Question, status chip with icon and word (queued, running, succeeded, failed), then claims. Each claim shows its
  provenance label; `ai_generated` claims carry the AI-generated mark and are visually distinct from `sourced`
  claims by the same line-style language as the map. Sources are listed under each claim.
- A failed run shows `failure_reason` and a retry. No partial answer is presented as complete.

### RepositorySummary (planned)

- Owner and repository in code type, description as text, language, licence, dates, and the health figures the API
  actually returns, each labelled with its calculation. Rate-limit state from `get_github_rate_limit`.

### SeedNotice

- A persistent, non-dismissible line at the bottom of the shell whenever the data mode is `seed`: the API's
  `data_notice` text ("Development seed data. Not verified production facts."). Uses `status-warn` text with an icon,
  and appears in the page's accessible description, not only visually.

### States

- **LoadingState.** Reserves the final layout; skeletons match the final dimensions; announces "Loading" politely
  only if it lasts longer than 400ms.
- **EmptyState.** `emptyState(kind, context)`: title, one sentence, optional action.
- **ErrorState.** `errorState(code)`: title, body, optional request ID, one action. `rate_limited` and
  `upstream_rate_limited` add `describeWait(retryAfterSeconds)`. `aborted` renders nothing.
- Errors that block a whole route use a full pane; errors inside a pane stay inside it.

## 4. Test expectations (UNVERIFIED until the app exists)

Each component ships with: a render test per state; a keyboard test for every key in its spec; an automated
accessibility check; and a test that its text comes from the tables above rather than literals. The checks that
already run today are listed in FRONTEND.md section 12.
