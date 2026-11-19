#!/usr/bin/env python3
"""Drive the browser prototype in Chromium: assertions on the DOM, console errors, and screenshots.

    python3 scripts/verify_prototype_browser.py                   # screenshots to docs/prototype/
    python3 scripts/verify_prototype_browser.py --out /some/dir
    python3 scripts/verify_prototype_browser.py --chromium /path/to/chromium

NOT part of scripts/verify_stdlib.sh: it needs the third-party ``playwright`` Python package and a
Chromium build, which are tooling here, not project dependencies, and it is UNVERIFIED in CI until a
pipeline installs them. It starts the prototype server itself (rate limiting off, because the run
reloads the page many times) and stops it afterwards. Build the scripts first:

    python3 scripts/build_prototype.py

Exit status 0 means every check passed. The screenshots are for people to look at; the checks do not
judge how the page looks, only what it does.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))



class Seed:
    """What the map must show, read from the seed itself, so the checks follow the data instead of a number."""

    def __init__(self) -> None:
        from vyron_api.domain.entities import EntityKind
        from vyron_api.seed.dev_seed import build_seed_graph, seed_timeline

        graph = build_seed_graph()
        # The year each dated entity first appears: its earliest event, as the strip reads it. Seed values.
        self.dated: dict[str, int] = {}
        self.titles: dict[int, list[str]] = {}
        self.names = {entity.id: entity.name for entity in graph.entities()}
        for event in sorted(seed_timeline(), key=lambda e: (e.occurred_on, e.id)):
            if event.entity_id not in self.dated:
                self.dated[event.entity_id] = event.occurred_on.year
                self.titles.setdefault(event.occurred_on.year, []).append(event.title)
        self.events = len(seed_timeline())
        self.first_year = min(self.dated.values())
        self.last_year = max(self.dated.values())
        self.entities = graph.entity_count
        self.relationships = graph.relationship_count
        self.clusters = {c.label: len(c.member_ids) for c in graph.clusters()}
        python = graph.find_entity(EntityKind.LANGUAGE, "python")
        assert python is not None
        links = graph.relationships_of(python.id)
        self.python_links = len(links)
        self.python_neighbours = len({r.target_id if r.source_id == python.id else r.source_id for r in links})
        assert self.clusters.get("Java") == 2, "the cluster check clicks the small, isolated 'Java' cluster"

    def later_than(self, year: int) -> int:
        """Dated entities whose first event is after `year`: the ones the map leaves out as of that year."""
        return sum(1 for first in self.dated.values() if first > year)

    @property
    def undated(self) -> int:
        return self.entities - len(self.dated)


SEED = Seed()
# The data-id of the mark farthest from every other mark: on a phone the middle of a 60-entity map is too
# crowded to tap one mark without hitting its neighbour's 32 px target, so a touch check uses a lone one.
MOST_ISOLATED_NODE = """() => {
  const nodes = [...document.querySelectorAll('.node')].map((n) => {
    const r = n.getBoundingClientRect();
    return { id: n.getAttribute('data-id'), x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  let best = null;
  for (const a of nodes) {
    const nearest = Math.min(...nodes.filter((b) => b !== a).map((b) => Math.hypot(a.x - b.x, a.y - b.y)));
    if (best === null || nearest > best.nearest) best = { id: a.id, nearest };
  }
  return best.id;
}"""
# A mark that a tap can reach on a phone: drawn, inside the part of the map not covered by the sheet, and as far as
# can be from the other drawn marks, so the tap cannot land on a neighbour's 32 px target.
TAPPABLE_NODE = """() => {
  const stage = document.getElementById('stage').getBoundingClientRect();
  const sheet = document.getElementById('dossier').getBoundingClientRect();
  const floor = sheet.height > 0 && sheet.top > stage.top ? Math.min(stage.bottom, sheet.top) : stage.bottom;
  const drawn = [...document.querySelectorAll('.node:not(.is-hidden)')].filter((n) => n.getClientRects().length > 0)
    .map((n) => { const r = n.getBoundingClientRect(); return { id: n.getAttribute('data-id'),
      x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  const free = drawn.filter((n) => n.x > stage.left + 16 && n.x < stage.right - 16
    && n.y > stage.top + 16 && n.y < floor - 16);
  let best = null;
  for (const a of free) {
    const near = Math.min(...drawn.filter((b) => b !== a).map((b) => Math.hypot(a.x - b.x, a.y - b.y)), 999);
    if (best === null || near > best.near) best = { ...a, near };
  }
  return best;
}"""
# Names that sit on top of a mark they do not belong to, on whatever is drawn now.
NAMES_OVER_MARKS = """() => {
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; };
  const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  const marks = [...document.querySelectorAll('.node:not(.is-hidden)')].filter((n) => n.getClientRects().length > 0)
    .map((n) => box(n.querySelector('.glyph')));
  const entity = [...document.querySelectorAll('.label:not(.is-off)')].map(box);
  const region = [...document.querySelectorAll('.territory:not(.is-hidden) .territory-name')].map(box);
  const over = (names) => names.filter((name) => marks.some((mark) => hit(name, mark))).length;
  return { entity: over(entity), region: over(region) };
}"""
OVERFLOW_X = "document.documentElement.scrollWidth > document.documentElement.clientWidth"
OUTSIDE_STAGE = """(selector) => {
  const stage = document.getElementById('stage').getBoundingClientRect();
  return [...document.querySelectorAll(selector)].filter((element) => {
    const r = element.getBoundingClientRect();
    return r.left < stage.left || r.right > stage.right || r.top < stage.top || r.bottom > stage.bottom;
  }).length;
}"""
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-background-networking",
    "--disable-sync",
    "--no-first-run",
    "--disable-features=OptimizationHints,AutofillServerCommunication",
]


class Run:
    """The checks made so far, the page under test, and where screenshots go."""

    def __init__(self, base: str, shots: Path, browser: Any) -> None:
        self.base = base
        self.shots = shots
        self.browser = browser
        self.results: list[tuple[str, bool, str]] = []
        self.messages: dict[int, list[str]] = {}

    def check(self, name: str, ok: object, detail: object = "") -> None:
        self.results.append((name, bool(ok), str(detail)))
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail != "" else ""), flush=True)

    def open(self, width: int, height: int, **options: Any) -> Any:
        context = self.browser.new_context(viewport={"width": width, "height": height}, **options)
        page = context.new_page()
        log: list[str] = []
        self.messages[id(page)] = log

        def note_console(message: Any) -> None:
            if message.type in ("error", "warning"):  # a CSP violation is reported as a console error
                log.append(f"console.{message.type}: {message.text}")

        page.on("console", note_console)
        page.on("pageerror", lambda e: log.append(f"pageerror: {e}"))
        page.on("requestfailed", lambda r: log.append(f"requestfailed: {r.url}"))
        return page

    def load(self, page: Any, path: str = "", ready: str = ".node") -> None:
        page.goto(self.base + path)
        page.wait_for_selector(ready, state="attached")  # a phone opens at cluster level, where marks are hidden
        page.wait_for_timeout(1200)

    def console(self, page: Any) -> list[str]:
        return self.messages.get(id(page), [])

    def shot(self, page: Any, name: str) -> None:
        page.screenshot(path=str(self.shots / f"{name}.png"))


def settle(page: Any, ms: int = 900) -> None:
    page.wait_for_timeout(ms)


def count(page: Any, selector: str) -> int:
    return page.locator(selector).count()


def title_of(page: Any) -> str:
    return page.locator("#dossier .title").first.inner_text()


def desktop(run: Run) -> None:
    page = run.open(1440, 900)
    run.load(page)
    check = run.check

    check(f"{SEED.entities} entities drawn", count(page, ".node") == SEED.entities, count(page, ".node"))
    edges, territories = count(page, ".edge"), count(page, ".territory")
    check(f"{SEED.relationships} relationships drawn", edges == SEED.relationships, edges)
    check(f"{len(SEED.clusters)} territories", territories == len(SEED.clusters), territories)
    labelled = count(page, ".label:not(.is-off)")
    # Labels that would collide are left off until the map is zoomed in; most must still be named at the start.
    named = f"{labelled} of {SEED.entities}"
    check("most entities are named at the opening view", labelled >= SEED.entities // 2, named)
    check("footer states the counts", f"{SEED.entities} entities" in page.locator("#footer-counts").inner_text())
    check("the seed notice is visible", "seed" in page.locator("#footer-notice").inner_text().lower())
    check("no horizontal overflow", not page.evaluate(OVERFLOW_X))
    check("no inline style attributes", page.evaluate("document.querySelectorAll('[style]').length") == 0)
    check("every node is inside the stage", page.evaluate(OUTSIDE_STAGE, ".node") == 0)
    check("every label is inside the stage", page.evaluate(OUTSIDE_STAGE, ".label:not(.is-off)") == 0)
    check("every region name is inside the stage", page.evaluate(OUTSIDE_STAGE, ".territory-name") == 0)
    over = page.evaluate(NAMES_OVER_MARKS)
    check("no entity name sits on another entity's mark", over["entity"] == 0, over)
    check("every node has a spoken description", page.evaluate(
        "[...document.querySelectorAll('.node')].every((n) => (n.getAttribute('aria-label') || '').length > 10)"))
    run.shot(page, "v01-desktop-open")

    # keyboard from the top of the page: the skip link comes first
    page.keyboard.press("Tab")
    check("the first Tab stop is the skip link", page.evaluate("document.activeElement.className") == "skip")

    # search
    page.keyboard.press("Control+k")
    check("Ctrl+K focuses the search box", page.evaluate("document.activeElement.id") == "search")
    page.keyboard.type("post")
    settle(page, 700)
    check("search finds PostgreSQL for 'post'", count(page, "#search-results [role=option]") == 1)
    check("the combobox reports it is expanded", page.get_attribute("#search", "aria-expanded") == "true")
    run.shot(page, "v02-search")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    settle(page, 1000)
    check("choosing a result selects that entity", count(page, ".node.is-selected") == 1)
    check("the address records the selection", "focus=" in page.url, page.url)
    check("the details show that entity", title_of(page) == "PostgreSQL", title_of(page))
    check("the selection is announced", "PostgreSQL" in page.locator("#live").inner_text())
    run.shot(page, "v03-selected-search")

    # leaving the selection
    page.keyboard.press("Escape")
    check("Escape in the search box clears the text", page.input_value("#search") == "")
    page.locator(".map").focus()
    page.keyboard.press("Escape")
    settle(page, 300)
    check("Escape on the map clears the selection", count(page, ".node.is-selected") == 0)
    check("clearing the selection brings back the introduction", title_of(page).lower().startswith("the living map"))
    page.go_back()
    settle(page, 600)
    check("the browser's back button returns to the selection", count(page, ".node.is-selected") == 1)
    page.go_forward()
    settle(page, 600)
    check("and forward clears it again", count(page, ".node.is-selected") == 0)

    # click an entity
    page.locator("button[aria-label^='Fit']").click()
    settle(page, 800)
    page.locator(".node", has=page.locator("title", has_text="Python (")).first.click()
    settle(page, 1000)
    check("clicking a node selects it", count(page, ".node.is-selected") == 1)
    check("its relationship lines are highlighted", count(page, ".edge.is-active") == SEED.python_links,
          count(page, ".edge.is-active"))
    check("its neighbours are marked related", count(page, ".node.is-related") == SEED.python_neighbours,
          count(page, ".node.is-related"))
    check("no two relationship phrases overlap", page.evaluate("""() => {
      const boxes = [...document.querySelectorAll('.edge-label:not(.is-off)')].map((e) => e.getBoundingClientRect());
      return boxes.every((a, i) => boxes.every((b, j) => i >= j ||
        a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top));
    }"""))
    run.shot(page, "v04-selected-python")

    # a connection in the details moves the selection
    page.locator("#dossier .sentences .link", has_text="NumPy").click()
    settle(page, 900)
    check("a connection link selects the other entity", title_of(page) == "NumPy", title_of(page))

    # a cluster
    page.locator(".map").focus()
    page.keyboard.press("Escape")
    page.locator("button[aria-label^='Fit']").click()
    settle(page, 800)
    java = page.locator(".territory").filter(has_text=re.compile(r"^\s*Java\s*\d+\s*$"))  # not JavaScript
    hull = java.locator(".hull").bounding_box()
    page.mouse.click(hull["x"] + hull["width"] / 2, hull["y"] + hull["height"] / 2)
    settle(page, 900)
    check("clicking a territory selects the cluster", count(page, ".territory.is-selected") == 1)
    check("the details name the cluster and its members", title_of(page) == "Java"
          and count(page, "#dossier .rows .row") == SEED.clusters["Java"])
    run.shot(page, "v05-cluster")

    # level of detail
    page.locator(".map").focus()
    page.keyboard.press("Escape")
    page.locator("button[aria-label^='Fit']").click()
    settle(page, 800)
    for _ in range(2):
        page.locator("button[aria-label^='Zoom out']").click()
        settle(page, 500)
    check("zooming out reaches the clusters level", page.get_attribute(".map", "data-lod") == "clusters")
    check("the clusters level hides entities", page.locator(".node").first.is_hidden())
    run.shot(page, "v06-clusters-level")
    for _ in range(8):
        if page.get_attribute(".map", "data-lod") == "labelled":
            break
        page.locator("button[aria-label^='Zoom in']").click()
        settle(page, 400)
    check("zooming in reaches the labelled level", page.get_attribute(".map", "data-lod") == "labelled")
    run.shot(page, "v07-zoomed-in")

    # the text outline
    page.locator("#view-outline").click()
    settle(page, 500)
    outline_shown = page.locator("#outline").is_visible() and not page.locator("#stage").is_visible()
    check("the outline replaces the map", outline_shown)
    check(f"the outline lists all {SEED.entities} entities", count(page, "#outline .outline-entity") == SEED.entities)
    check("the address carries view=outline", "view=outline" in page.url, page.url)
    run.shot(page, "v08-outline")
    page.reload()
    page.wait_for_selector("#outline .outline-entity")
    check("the outline survives a reload", page.locator("#outline").is_visible())
    page.locator("#outline .entity-link", has_text="TensorFlow").first.click()
    settle(page, 500)
    check("choosing an entity in the outline selects it", title_of(page) == "TensorFlow", title_of(page))
    page.locator("#view-map").click()
    settle(page, 700)

    # keyboard navigation on the map
    page.locator(".node").first.focus()
    check("a node can take focus", page.evaluate("document.activeElement.classList.contains('node')"))
    before = page.evaluate("document.activeElement.getAttribute('data-id')")
    moved = False
    for key in ("ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"):  # a node at an edge has nothing in some directions
        page.keyboard.press(key)
        settle(page, 200)
        after = page.evaluate("document.activeElement.getAttribute('data-id')")
        if after != before:
            moved = True
            break
    check("arrow keys move focus between nodes", moved, f"{before} -> {after} (last key {key})")
    page.keyboard.press("Enter")
    settle(page, 800)
    check("Enter selects the focused node", count(page, ".node.is-selected") == 1)
    run.shot(page, "v09-keyboard-focus")
    check("the desktop console is clean (no errors, no CSP violations)", not run.console(page), run.console(page))
    page.context.close()


def set_year(page: Any, year: int) -> None:
    """What a finger or a pointer does to the slider: set it and say so, as the browser does for both events."""
    page.locator(".time-slider").evaluate(
        "(slider, year) => { slider.value = String(year); slider.dispatchEvent(new Event('input', { bubbles: true }));"
        " slider.dispatchEvent(new Event('change', { bubbles: true })); }",
        year,
    )


def slider_to(page: Any, year: int) -> None:
    """The keyboard route: Home, then one Right arrow per year."""
    page.locator(".time-slider").focus()
    page.keyboard.press("Home")
    for _ in range(year - SEED.first_year):
        page.keyboard.press("ArrowRight")
    settle(page, 600)


def timeline(run: Run) -> None:
    check = run.check
    dated, first, last, year = len(SEED.dated), SEED.first_year, SEED.last_year, 1995
    later, present = SEED.later_than(year), dated - SEED.later_than(year)

    page = run.open(1440, 900)
    run.load(page)
    page.wait_for_selector(".time-mark", state="attached")
    check("timeline: the strip is shown under the map", page.locator("#time").is_visible()
          and page.get_attribute(".time-toggle", "aria-expanded") == "true")
    badge = page.locator(".time-badges .badge")
    seeded = badge.count() == 1 and badge.get_attribute("data-provenance") == "seed"
    check("timeline: it labels the dates as development seed data", seeded and "seed" in badge.inner_text().lower(),
          badge.all_inner_texts())
    span = (page.get_attribute(".time-slider", "min"), page.get_attribute(".time-slider", "max"))
    check("timeline: the slider spans the first to the last dated year", span == (str(first), str(last)), span)
    check("timeline: one mark for each dated entity", count(page, ".time-mark") == dated, count(page, ".time-mark"))
    status = page.locator(".time-status").inner_text()
    check("timeline: it opens on every year", status.startswith(f"Every year, {first}\u2013{last}.")
          and f"{dated} of {SEED.entities} entities" in status, status)
    check("timeline: at first nothing is left out or dimmed",
          count(page, ".node.is-hidden") == 0 and count(page, ".node.is-undated") == 0)
    check("timeline: the strip stays inside the page", not page.evaluate(OVERFLOW_X))

    slider_to(page, year)
    check(f"timeline: {year} leaves out the {later} entities that came later",
          count(page, ".node.is-hidden") == later, count(page, ".node.is-hidden"))
    check(f"timeline: the {SEED.undated} without a date stay, dimmed", count(page, ".node.is-undated") == SEED.undated,
          count(page, ".node.is-undated"))
    status = page.locator(".time-status").inner_text()
    said = f"Map as of {year}: {present} of {dated} dated entities. {SEED.undated} without a date are shown dimmed."
    check("timeline: the status line says exactly that", said in status, status)
    check("timeline: the address records the year", f"year={year}" in page.url, page.url)
    check("timeline: the slider reads out the year", str(year) in page.get_attribute(".time-slider", "aria-valuetext"),
          page.get_attribute(".time-slider", "aria-valuetext"))
    line = page.locator(".time-year-line").inner_text()
    named = {button.strip() for button in page.locator(".time-year-line .time-entity").all_inner_texts()}
    check(f"timeline: it names what is dated {year}",
          line.startswith(f"Dated {year}:") and named == set(SEED.titles[year]),
          f"{sorted(named)} vs {sorted(SEED.titles[year])}")
    check("timeline: later marks on the strip fade", count(page, ".time-mark.is-later") == later,
          count(page, ".time-mark.is-later"))
    check("timeline: relationship lines to what is left out go too", count(page, ".edge.is-hidden") > 0)
    counts = page.locator(".territory-count").all_text_contents()
    partial = any(re.fullmatch(r"\d+ of \d+", c.strip()) for c in counts)
    check("timeline: a region shows how many of its entities are left", partial, counts)
    page.locator(".time-notes summary").click()
    notes = page.locator(".time-notes").inner_text()
    check("timeline: the notes say how dates are placed and what undated entities do",
          "earliest event" in notes and "no date on the timeline" in notes and "not placed" not in notes, notes)
    page.locator(".time-notes summary").click()
    run.shot(page, "v34-timeline-1995")

    # the address alone restores it
    page.reload()
    page.wait_for_selector(".time-mark", state="attached")
    settle(page, 900)
    check("timeline: reloading keeps the year", count(page, ".node.is-hidden") == later
          and page.get_attribute(".time-slider", "min") == str(first) and page.locator(".time-reset").is_visible())
    value = page.input_value(".time-slider")
    check("timeline: the slider is back on that year", value == str(year), value)

    # a name in the year line selects the entity and keeps the year
    page.locator(".time-year-line .time-entity", has_text="Java announced").click()
    settle(page, 900)
    check("timeline: a name in the year line selects that entity", title_of(page) == "Java"
          and count(page, ".node.is-selected") == 1, title_of(page))
    check("timeline: the year stays", f"year={year}" in page.url and count(page, ".node.is-hidden") == later, page.url)

    # moving the year back past the selection clears it
    page.locator(".time-slider").focus()
    page.keyboard.press("ArrowLeft")
    settle(page, 700)
    check("timeline: going back past the selected entity clears it", count(page, ".node.is-selected") == 0
          and title_of(page).lower().startswith("the living map"), title_of(page))
    page.keyboard.press("ArrowRight")
    settle(page, 500)

    # choosing an entity that came later widens the year and says so
    page.keyboard.press("Control+k")
    page.keyboard.type("post")
    settle(page, 800)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    settle(page, 1000)
    check("timeline: finding a later entity selects it", title_of(page) == "PostgreSQL", title_of(page))
    widened = "year=" not in page.url and count(page, ".node.is-hidden") == 0
    check("timeline: and shows every year again", widened, page.url)
    live = page.locator("#live").inner_text()
    check("timeline: it says why", "Showing every year" in live and "after" in live, live)

    # back to one year, then the reset button
    slider_to(page, year)
    check("timeline: the year can be chosen again", count(page, ".node.is-hidden") == later)
    page.locator(".time-reset").click()
    settle(page, 700)
    check("timeline: Show every year puts everything back", count(page, ".node.is-hidden") == 0
          and "year=" not in page.url and page.locator(".time-reset").is_hidden())

    # folding the strip gives the room to the map
    before = page.locator("#stage").bounding_box()["height"]
    page.locator(".time-toggle").click()
    settle(page, 700)
    after = page.locator("#stage").bounding_box()["height"]
    check("timeline: folding the strip hides it and gives the map the room",
          page.get_attribute(".time-toggle", "aria-expanded") == "false" and page.locator("#time-body").is_hidden()
          and after > before, f"{before} -> {after}")
    page.locator(".time-toggle").click()
    settle(page, 700)
    check("timeline: unfolding draws the marks again", count(page, ".time-mark") == dated)

    # the outline is not a map as of a year
    page.locator("#view-outline").click()
    settle(page, 400)
    check("timeline: the outline has no strip", page.locator("#time").is_hidden())
    page.locator("#view-map").click()
    settle(page, 600)
    check("timeline: the map brings it back", page.locator("#time").is_visible() and count(page, ".time-mark") == dated)
    check("timeline: the desktop console is clean", not run.console(page), run.console(page))
    page.context.close()

    # an address with a year the dates do not reach
    page = run.open(1440, 900)
    run.load(page, "?year=1950")
    page.wait_for_selector(".time-mark", state="attached")
    settle(page, 700)
    notice = page.locator("#url-notice")
    check("timeline: a year before the first date is moved to it and said so",
          notice.is_visible() and "before the first dated event" in notice.inner_text()
          and page.input_value(".time-slider") == str(first), notice.inner_text())
    check("timeline: the address says the year that was used", f"year={first}" in page.url, page.url)
    run.load(page, "?year=2100")
    page.wait_for_selector(".time-mark", state="attached")
    settle(page, 700)
    notice = page.locator("#url-notice")
    check("timeline: a year after the last date shows every year and says so",
          notice.is_visible() and "after the last dated event" in notice.inner_text()
          and count(page, ".node.is-hidden") == 0 and "year=" not in page.url, notice.inner_text())
    page.context.close()

    # the dates cannot be read: the map still works and the strip says what is missing
    page = run.open(1440, 900)
    page.route(re.compile(r"/api/v1/timeline(\?|$)"), lambda route: route.abort())
    page.goto(run.base)
    page.wait_for_selector(".node", state="attached")
    settle(page, 1500)
    check("timeline failure: the map is still drawn", count(page, ".node") == SEED.entities)
    failure = page.locator(".time-status").inner_text()
    check("timeline failure: the strip says the dates could not be read", page.locator(".time-failure").is_visible()
          and "not filtered by year" in failure and count(page, ".time-mark") == 0, failure)
    again = page.locator(".time-status button", has_text="Try again")
    check("timeline failure: it offers to try again", again.count() == 1)
    run.shot(page, "v35-timeline-failure")
    page.unroute(re.compile(r"/api/v1/timeline(\?|$)"))
    again.click()
    page.wait_for_selector(".time-mark", state="attached", timeout=8000)
    check("timeline failure: trying again after recovery draws the strip", count(page, ".time-mark") == dated)
    page.context.close()


def medium(run: Run) -> None:
    page = run.open(900, 800)
    run.load(page)
    check = run.check
    check("medium: the Layers button is shown", page.locator("#layers-toggle").is_visible())
    check("medium: the rail is hidden until asked for", not page.locator("#rail").is_visible())
    run.shot(page, "v10-medium")
    page.locator("#layers-toggle").click()
    settle(page, 500)
    check("medium: Layers opens the rail", page.locator("#rail").is_visible())
    run.shot(page, "v11-medium-layers")
    page.keyboard.press("Escape")
    settle(page, 500)
    check("medium: Escape closes the rail", not page.locator("#rail").is_visible())
    page.locator(".node").nth(3).click()
    settle(page, 900)
    check("medium: the details overlay the map on selection", page.locator("#dossier").is_visible())
    check("medium: labels stay out from under the details", page.evaluate("""() => {
      const panel = document.getElementById('dossier').getBoundingClientRect();
      return [...document.querySelectorAll('.label:not(.is-off)')]
        .every((e) => e.getBoundingClientRect().right <= panel.left + 1);
    }"""))
    run.shot(page, "v12-medium-selected")
    check("medium: no horizontal overflow", not page.evaluate(OVERFLOW_X))
    check("medium: the console is clean", not run.console(page), run.console(page))
    page.context.close()


def mobile(run: Run) -> None:
    page = run.open(390, 844, has_touch=True, is_mobile=True, device_scale_factor=2)
    run.load(page)
    check = run.check
    check("mobile: no horizontal overflow", not page.evaluate(OVERFLOW_X))
    check("mobile: the map opens at the regions, not on 60 crowded marks",
          page.get_attribute(".map", "data-lod") == "clusters", page.get_attribute(".map", "data-lod"))
    bar = page.locator(".bar").bounding_box()
    check("mobile: the top bar takes two rows", bar and bar["height"] < 130, bar)
    box = page.locator("#search").bounding_box()
    check("mobile: the search box is inside the screen", box and box["x"] >= 0 and box["x"] + box["width"] <= 390, box)
    check("mobile: the search box asks in few words", page.get_attribute("#search", "placeholder") == "Search the map")
    in_bar = page.locator(".bar > .bar-repository")
    check("mobile: the Repository and Dependencies links are not in the bar",
          in_bar.count() == 2 and not any(link.is_visible() for link in in_bar.all()), in_bar.count())
    check("mobile: every region name is inside the stage", page.evaluate(OUTSIDE_STAGE, ".territory-name") == 0)
    check("mobile: the timeline strip is there and folded", page.locator("#time").is_visible()
          and page.get_attribute(".time-toggle", "aria-expanded") == "false" and page.locator("#time-body").is_hidden())
    run.shot(page, "v13-mobile")

    page.locator("#layers-toggle").tap()
    settle(page, 500)
    check("mobile: Layers opens the rail", page.locator("#rail").is_visible())
    links = page.locator(".rail-pages a")
    targets = [link.get_attribute("href") for link in links.all() if link.is_visible()]
    check("mobile: the rail carries the Repository and Dependencies links",
          targets == ["/repository.html", "/dependencies.html"], targets)
    run.shot(page, "v15-mobile-layers")
    page.keyboard.press("Escape")
    settle(page, 500)
    check("mobile: Escape closes the rail", not page.locator("#rail").is_visible())

    java = page.locator(".territory").filter(has_text=re.compile(r"^\s*Java\s*\d+\s*$"))
    hull = java.locator(".hull").bounding_box()
    page.touchscreen.tap(hull["x"] + hull["width"] / 2, hull["y"] + hull["height"] / 2)
    settle(page, 1200)
    check("mobile: tapping a region selects it", count(page, ".territory.is-selected") == 1)
    check("mobile: selecting shows the details sheet", page.locator("#dossier").is_visible())
    sheet = page.locator("#dossier").bounding_box()
    check("mobile: the sheet leaves at least half the screen for the map", sheet and sheet["height"] <= 844 * 0.5)
    check("mobile: tapping a region zooms in to its entities", page.get_attribute(".map", "data-lod") != "clusters",
          page.get_attribute(".map", "data-lod"))
    shown = page.locator(".territory.is-selected .hull").bounding_box()
    stage = page.locator("#stage").bounding_box()
    check("mobile: the region is framed above the sheet", shown and shown["y"] >= stage["y"] - 1
          and shown["y"] + shown["height"] <= sheet["y"] + 1, f"hull {shown}, sheet top {sheet['y']}")
    covered = page.get_attribute("#time", "inert") is not None
    check("mobile: the strip is out of reach while the sheet covers it", covered)
    check("mobile: no horizontal overflow with the sheet open", not page.evaluate(OVERFLOW_X))
    run.shot(page, "v36-mobile-region")

    target = page.evaluate(TAPPABLE_NODE)
    check("mobile: a mark can be tapped above the sheet", target is not None, target)
    if target is not None:
        page.touchscreen.tap(target["x"], target["y"])
        settle(page, 1200)
        check("mobile: tapping a mark selects that entity", count(page, ".node.is-selected") == 1
              and page.get_attribute(".node.is-selected", "data-id") == target["id"], title_of(page))
        mark = page.locator(".node.is-selected").bounding_box()
        sheet = page.locator("#dossier").bounding_box()
        check("mobile: the selected mark is in view above the sheet", mark and mark["y"] >= stage["y"]
              and mark["y"] + mark["height"] <= sheet["y"] and 0 <= mark["x"] <= 390 - mark["width"], f"{mark} {sheet}")
    run.shot(page, "v14-mobile-selected")

    page.locator("#dossier button[aria-label^='Close']").tap()
    settle(page, 800)
    check("mobile: closing the sheet gives the strip back", page.get_attribute("#time", "inert") is None)
    page.locator(".time-toggle").tap()
    settle(page, 700)
    check("mobile: the strip opens", page.get_attribute(".time-toggle", "aria-expanded") == "true"
          and page.locator("#time-body").is_visible())
    pad = page.eval_on_selector("#time", "e => getComputedStyle(e).paddingLeft")
    bar_pad = page.eval_on_selector(".bar", "e => getComputedStyle(e).paddingLeft")
    check("mobile: the strip lines up with the bar's side margins", pad == bar_pad, f"strip {pad}, bar {bar_pad}")
    set_year(page, 1995)
    settle(page, 700)
    check("mobile: choosing a year leaves out what came later", count(page, ".node.is-hidden") == SEED.later_than(1995),
          count(page, ".node.is-hidden"))
    check("mobile: the address records the year", "year=1995" in page.url, page.url)
    check("mobile: the status line says so", f"Map as of 1995: {len(SEED.dated) - SEED.later_than(1995)} of"
          in page.locator(".time-status").inner_text(), page.locator(".time-status").inner_text())
    run.shot(page, "v37-mobile-timeline")
    check("mobile: no horizontal overflow with the strip open", not page.evaluate(OVERFLOW_X))
    check("mobile: the console is clean", not run.console(page), run.console(page))
    page.context.close()

    page = run.open(375, 667, has_touch=True, is_mobile=True)
    run.load(page)
    box = page.locator("#search").bounding_box()
    inside = box and box["x"] >= 0 and box["x"] + box["width"] <= 375
    check("small phone: the search box is inside the screen", inside, box)
    check("small phone: no horizontal overflow", not page.evaluate(OVERFLOW_X))
    check("small phone: every region name is inside the stage", page.evaluate(OUTSIDE_STAGE, ".territory-name") == 0)
    page.context.close()


def other_conditions(run: Run) -> None:
    check = run.check

    page = run.open(1440, 900, reduced_motion="reduce")
    run.load(page)
    page.locator(f".node[data-id='{page.evaluate(MOST_ISOLATED_NODE)}']").click()
    settle(page, 150)
    check("reduced motion: the selection settles at once", count(page, ".node.is-selected") == 1)
    page.context.close()

    page = run.open(1440, 900)
    page.route("**/api/v1/**", lambda route: route.abort())
    page.goto(run.base)
    settle(page, 1500)
    state = page.locator("#stage-state")
    check("API failure: the stage explains it", state.is_visible() and "Cannot reach" in state.inner_text())
    check("API failure: it offers to try again", count(page, "#stage-state button") == 1)
    check("API failure: no map is drawn", count(page, ".node") == 0)
    run.shot(page, "v16-api-failure")
    page.unroute("**/api/v1/**")
    page.locator("#stage-state button").click()
    page.wait_for_selector(".node", timeout=8000)
    check("API failure: trying again after recovery draws the map", count(page, ".node") == SEED.entities)
    page.context.close()

    page = run.open(1440, 900)
    run.load(page, "?focus=not-a-real-id&view=sideways")
    check("bad address parameters: the map still loads", count(page, ".node") == SEED.entities)
    notice = page.locator("#url-notice")
    check("bad address parameters: the user is told what was ignored",
          notice.is_visible() and "focus" in notice.inner_text() and "view" in notice.inner_text(), notice.inner_text())
    cleaned = "sideways" not in page.url and "not-a-real-id" not in page.url
    check("bad address parameters: the address is cleaned", cleaned, page.url)
    run.shot(page, "v17-bad-url")
    run.load(page, "?focus=00000000-0000-4000-8000-000000000000")
    details = page.locator("#dossier").inner_text()
    check("unknown entity id: the details say it is not on the map", "not on this map" in details)
    check("unknown entity id: nothing is selected", count(page, ".node.is-selected") == 0)
    page.context.close()


# ---- the repository page, against the GitHub stand-in -----------------------------------------------------
#
# EVERYTHING BELOW READS FIXTURE DATA FROM scripts/standin_github.py, NOT GITHUB. The fixture repositories belong to
# a made-up organisation and say so in their descriptions, so a screenshot taken here labels itself. A pass shows the
# page behaves correctly when GitHub answers in the shapes the stand-in serves; it does not show GitHub sends them.

HTTP_NOISE = "console.error: Failed to load resource"


def standin_requests(standin: str) -> list[str]:
    """What the stand-in was asked for, apart from the free quota read."""
    with urllib.request.urlopen(standin + "/__standin/requests", timeout=5) as response:  # noqa: S310
        return [entry["path"] for entry in json.load(response) if entry["path"] != "/rate_limit"]


def standin_reset(standin: str) -> None:
    urllib.request.urlopen(standin + "/__standin/reset", timeout=5).close()  # noqa: S310


def only_http_noise(messages: list[str]) -> bool:
    """A 404 or 502 the page was meant to receive is reported by the browser as a console error; nothing else is."""
    return all(message.startswith(HTTP_NOISE) for message in messages)


def analyze(page: Any, text: str) -> None:
    page.fill("#repo-input", text)
    page.click("#repo-submit")
    page.wait_for_selector("#reading .report, #reading .failure", timeout=20000)
    page.wait_for_function("document.getElementById('repo-submit').disabled === false", timeout=20000)


def focused_id(page: Any) -> str:
    return page.evaluate("document.activeElement ? document.activeElement.id : ''")


def failure_of(page: Any, name: str) -> tuple[str, str]:
    analyze(page, f"fixture-org/{name}")
    return page.locator(".failure-title").inner_text(), page.locator(".failure").inner_text()


def repository_page(run: Run, standin: str) -> None:
    check = run.check
    standin_reset(standin)

    page = run.open(1280, 900)
    run.load(page, "repository.html", ready="#reading .idle")
    check("repository: it opens on an invitation", page.locator("#reading-title").inner_text() == "Read a repository")
    check("repository: it offers three examples", count(page, ".example") == 3)
    check("repository: opening it has not asked GitHub for anything", standin_requests(standin) == [])
    check("repository: the page does not overflow sideways", not page.evaluate(OVERFLOW_X))
    check("repository: the console is clean when idle", not run.console(page), run.console(page))
    run.shot(page, "v20-repository-idle")

    page.fill("#repo-input", "")
    page.click("#repo-submit")
    page.wait_for_timeout(200)
    check("repository: an empty form asks nothing and keeps the cursor in the box",
          standin_requests(standin) == [] and focused_id(page) == "repo-input")  # fmt: skip

    analyze(page, "fixture-org/example-repo")
    name = page.locator(".repo-name").inner_text()
    check("repository: a healthy repository is read and named", "fixture-org" in name and "example-repo" in name, name)
    check("repository: the fixture labels itself", "not data from GitHub" in page.locator(".repo-desc").inner_text())
    check("repository: it says the read was live", page.locator(".cache-label").inner_text() == "Live")
    ledger = page.locator(".ledger").inner_text()
    counts = all(figure in ledger for figure in ("1,234", "56", "7"))
    check("repository: the counts are the ones the stand-in sent", counts, ledger)
    shares = page.locator(".langs tbody tr")
    top = shares.nth(0).inner_text() if shares.count() else ""
    check("repository: three languages with their shares", shares.count() == 3 and "Python" in top and "70.0%" in top)
    check("repository: it carries a provenance badge", "Sourced" in page.locator(".provenance-band").inner_text())
    links = page.locator(".evidence a")
    hrefs = [links.nth(i).get_attribute("href") or "" for i in range(links.count())]
    exact = len(hrefs) == 2 and all(h.startswith(standin + "/repos/fixture-org/example-repo") for h in hrefs)
    check("repository: the evidence is the two exact URLs it read", exact, hrefs)
    named = unquote(page.url).endswith("?repo=fixture-org/example-repo")
    check("repository: the address names the repository", named, page.url)
    check("repository: focus moves to the repository's name", focused_id(page) == "reading-title", focused_id(page))
    check("repository: the quota is shown", "GitHub requests left" in page.locator("#quota").inner_text())
    check("repository: nothing overflows sideways", not page.evaluate(OVERFLOW_X))
    check("repository: the console is clean after a read", not run.console(page), run.console(page))
    run.shot(page, "v21-repository-fixture-report")

    before = standin_requests(standin)
    check("repository: reading it cost GitHub two requests", len(before) == 2, before)
    page.get_by_role("button", name="Check again").click()
    page.wait_for_selector("#reading .report", timeout=20000)
    check("repository: checking again within a minute does not reach GitHub", standin_requests(standin) == before)

    analyze(page, "https://github.com/fixture-org/example-repo/tree/main/docs")
    note = page.locator(".note-line")
    check("repository: a link into the repository reads the repository and says so",
          note.count() == 1 and "Analyzing fixture-org/example-repo" in note.inner_text())  # fmt: skip
    check("repository: that link was served without asking GitHub again", standin_requests(standin) == before)

    analyze(page, "fixture-org/quiet-repo")
    quiet = page.locator("#reading").inner_text()
    check("repository: a bare repository is described as bare, not blank",
          all(t in quiet for t in ("No description.", "Archived", "No license identified by GitHub", "No topics set.",
                                   "reports no languages")), quiet)  # fmt: skip
    run.shot(page, "v22-repository-fixture-bare")
    page.go_back()
    page.wait_for_selector("#reading .report", timeout=20000)
    check("repository: Back returns to the repository before it",
          "example-repo" in page.locator(".repo-name").inner_text(), page.url)  # fmt: skip

    page.press("#repo-input", "Enter")  # the box holds the repository's name again; Enter must submit it
    page.wait_for_selector("#reading .report", timeout=20000)
    check("repository: Enter in the box submits", count(page, ".report") == 1)

    for scenario, title, role in (
        ("private-repo", "No public repository called fixture-org/private-repo", "alert"),
        ("missing-repo", "No public repository called fixture-org/missing-repo", "alert"),
        ("moved-repo", "fixture-org/moved-repo has moved", "alert"),
    ):
        got, _ = failure_of(page, scenario)
        check(f"repository: {scenario} is explained", got == title, got)
        announced = page.locator(".failure").get_attribute("role") == role
        check(f"repository: {scenario} is announced as an alert", announced)
        check(f"repository: {scenario} offers no pointless retry", count(page, ".failure button") == 0)
        check(f"repository: {scenario} puts focus on the explanation", focused_id(page) == "reading-title")

    got, whole = failure_of(page, "gateway-repo")
    check("repository: a gateway refusal is reported as GitHub not giving a usable answer",
          got == "GitHub did not give a usable answer", got)  # fmt: skip
    check("repository: it never repeats what the upstream said", "No linked GitHub account" not in whole, whole)
    check("repository: it offers to try again", count(page, ".failure button") == 1)
    check("repository: it names the request for a bug report", "Request " in whole, whole)
    run.shot(page, "v23-repository-fixture-gateway")
    got, _ = failure_of(page, "broken-repo")
    check("repository: a server error is reported the same way", got == "GitHub did not give a usable answer", got)
    got, _ = failure_of(page, "garbled-repo")
    check("repository: an answer that is not JSON is reported the same way",
          got == "GitHub did not give a usable answer", got)  # fmt: skip
    check("repository: failures leave only the browser's own status messages in the console",
          only_http_noise(run.console(page)), run.console(page))  # fmt: skip

    standin_reset(standin)
    analyze(page, "fixture-org/flaky-repo")
    attempts = [p for p in standin_requests(standin) if p == "/repos/fixture-org/flaky-repo"]
    check("repository: one hiccup from GitHub is retried and the read still succeeds",
          count(page, ".report") == 1 and len(attempts) == 2, attempts)  # fmt: skip

    analyze(page, "fixture-org/other-license-repo")
    tags = page.locator(".tag").all_inner_texts()
    check("repository: a license GitHub could not name is shown by its name, with no identifier",
          "License Other (no standard identifier)" in tags and not any("declared" in tag for tag in tags),
          tags)  # fmt: skip

    before = standin_requests(standin)
    for text in ("hello world", "https://example.com/some/thing", "https://github.com/fixture-org"):
        analyze(page, text)
        check(f"repository: {text!r} is turned away with a reason",
              count(page, ".failure") == 1 and page.locator(".failure-title").inner_text() != "", text)  # fmt: skip
    check("repository: none of those reached GitHub", standin_requests(standin) == before)
    page.context.close()

    page = run.open(1280, 900)
    run.load(page, "repository.html?repo=fixture-org/example-repo", ready="#reading .report")
    check("repository: an address with ?repo= opens straight on the reading",
          "example-repo" in page.locator(".repo-name").inner_text())  # fmt: skip
    run.load(page, "repository.html?repo=%3Cscript%3Ealert(1)%3C/script%3E", ready="#reading .failure")
    check("repository: a hostile address value is shown as text and nothing runs",
          count(page, "#reading script") == 0 and page.locator(".failure-title").inner_text() != "")  # fmt: skip
    page.context.close()

    page = run.open(375, 740)
    run.load(page, "repository.html?repo=fixture-org/example-repo", ready="#reading .report")
    check("repository: on a phone nothing overflows sideways", not page.evaluate(OVERFLOW_X))
    bar = page.eval_on_selector(".bar", "e => e.getBoundingClientRect().height")
    check("repository: on a phone the top bar holds its form without covering the page", bar < 140, bar)
    box = page.locator(".repo-name").bounding_box() or {"y": 0}
    check("repository: on a phone the repository's name starts below the top bar", box["y"] >= bar, (box, bar))
    check("repository: on a phone both the box and the button are reachable",
          page.locator("#repo-input").is_visible() and page.locator("#repo-submit").is_visible())  # fmt: skip
    run.shot(page, "v24-repository-fixture-phone")
    page.context.close()

    page = run.open(1280, 900, reduced_motion="reduce", forced_colors="active")
    run.load(page, "repository.html?repo=fixture-org/example-repo", ready="#reading .report")
    check("repository: it works in forced-colors mode", count(page, ".share-fill") == 3 and count(page, ".report") == 1)
    page.context.close()

    page = run.open(1280, 900)
    run.load(page, "repository.html?repo=fixture-org/limited-repo", ready="#reading .failure")
    title = page.locator(".failure-title").inner_text()
    check("repository: a used-up quota is explained", title == "GitHub's rate limit was reached", title)
    check("repository: it says how long to wait", "Wait" in page.locator(".failure").inner_text())
    quota = page.locator("#quota")
    check("repository: the quota line turns to a warning",
          quota.get_attribute("class") == "warn" and "0 of 60" in quota.inner_text())  # fmt: skip
    before = standin_requests(standin)
    page.get_by_role("button", name="Try again").click()
    page.wait_for_selector("#reading .failure", timeout=20000)
    page.wait_for_timeout(500)
    check("repository: trying again while limited does not spend GitHub's last requests",
          standin_requests(standin) == before)  # fmt: skip
    run.shot(page, "v25-repository-fixture-rate-limited")
    page.context.close()


def open_activity(page: Any, run: Run, name: str) -> None:
    run.load(page, f"repository.html?repo=fixture-org/{name}", ready="#reading .report")


def load_activity(page: Any) -> None:
    page.get_by_role("button", name="Load contributors and latest release").click()
    page.wait_for_selector(".activity .activity-result, .activity .failure", timeout=20000)


def repository_activity(run: Run, standin: str) -> None:
    """The opt-in contributors and latest-release section. FIXTURE DATA from the stand-in, never GitHub."""
    check = run.check
    standin_reset(standin)
    page = run.open(1280, 900)
    open_activity(page, run, "example-repo")
    quiet = standin_requests(standin)
    check("activity: the section is there but has asked GitHub for nothing",
          count(page, ".activity") == 1 and len(quiet) == 2
          and count(page, ".activity-result") == 0, quiet)  # fmt: skip
    prompt = page.locator(".activity").inner_text()
    check("activity: it says what asking costs before it is asked", "twice more, or three times" in prompt, prompt)
    check("activity: the button is a real button the keyboard can reach",
          page.get_by_role("button", name="Load contributors and latest release").count() == 1)  # fmt: skip
    check("activity: the cost note is tied to the button for a screen reader",
          page.locator(".activity .primary").get_attribute("aria-describedby") == "activity-cost")  # fmt: skip

    page.focus(".activity .primary")
    page.keyboard.press("Enter")  # the keyboard path, not a click
    page.wait_for_selector(".activity .activity-result", timeout=20000)
    asked = standin_requests(standin)
    check("activity: pressing it asked for the contributors and the latest release, and nothing else",
          asked[2:] == ["/repos/fixture-org/example-repo/contributors",
                        "/repos/fixture-org/example-repo/releases/latest"], asked)  # fmt: skip
    rows = page.locator(".people tbody tr")
    check("activity: three accounts are listed, with GitHub's counts", rows.count() == 3
          and "fixture-person-a" in rows.nth(0).inner_text() and "120 commits" in rows.nth(0).inner_text())  # fmt: skip
    link = page.locator(".people tbody tr").nth(0).locator("a")
    check("activity: an account links to its github.com page",
          link.get_attribute("href") == "https://github.com/fixture-person-a")  # fmt: skip
    bot = rows.nth(2)
    check("activity: a bot is marked as one and is not linked",
          "bot" in bot.inner_text() and bot.locator("a").count() == 0)  # fmt: skip
    check("activity: nothing is loaded from GitHub's image servers",
          count(page, ".activity img") == 0 and "avatars.githubusercontent" not in page.content())  # fmt: skip
    release = page.locator(".release").inner_text()
    check("activity: the latest release is shown with its tag and date",
          "v1.2.3" in release and "1 Sep 2026" in release, release)  # fmt: skip
    check("activity: its link is the repository's own release page",
          page.locator(".release a").get_attribute("href")
          == "https://github.com/fixture-org/example-repo/releases/tag/v1.2.3")  # fmt: skip
    text = page.locator(".activity").inner_text()
    check("activity: it says what 'latest' means and what a commit count does not",
          "not necessarily the highest version number" in text
          and "says nothing about the quality" in text)  # fmt: skip
    check("activity: it carries a provenance badge and says the read was live",
          "Sourced" in text and page.locator(".activity .cache-label").inner_text() == "Live")  # fmt: skip
    hrefs = page.locator(".activity .evidence a").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    exact = len(hrefs) == 3 and all(h.startswith(standin + "/repos/fixture-org/example-repo") for h in hrefs)
    check("activity: the evidence is the three exact URLs it read", exact, hrefs)
    check("activity: focus moves to the section's heading", focused_id(page) == "activity-title", focused_id(page))
    page.wait_for_function("document.getElementById('quota').textContent.startsWith('56 of 60')", timeout=5000)
    check("activity: the quota line shows what the extra reads spent",
          "56 of 60" in page.locator("#quota").inner_text())  # fmt: skip
    announced = page.locator("#live").inner_text()
    said = "3 contributors listed, most commits fixture-person-a; latest release v1.2.3"
    check("activity: a screen reader is told what loaded", said in announced, announced)
    check("activity: nothing overflows sideways", not page.evaluate(OVERFLOW_X))
    check("activity: the console is clean", not run.console(page), run.console(page))
    run.shot(page, "v27-repository-fixture-activity")

    before = standin_requests(standin)
    page.get_by_role("button", name="Check again").last.click()
    page.wait_for_selector(".activity .activity-result", timeout=20000)
    check("activity: checking again within a minute does not reach GitHub", standin_requests(standin) == before)
    page.context.close()

    for name, expected, forbidden in (
        ("quiet-repo", ("GitHub lists no contributors for this repository.",
                        "reports no published release"), ".people"),
        ("huge-repo", ("GitHub declined to list this repository's contributors.", "v1.2.3"), ".people"),
        ("unreleased-repo", ("fixture-person-a", "reports no published release"), ".release"),
    ):
        page = run.open(1280, 900)
        open_activity(page, run, name)
        load_activity(page)
        shown = page.locator(".activity").inner_text()
        check(f"activity: {name} says what GitHub said", all(t in shown for t in expected), shown)
        check(f"activity: {name} does not draw what GitHub did not give", count(page, f".activity {forbidden}") == 0)
        check(f"activity: {name} is not presented as an error", count(page, ".activity .failure") == 0)
        if name == "huge-repo":
            run.shot(page, "v28-repository-fixture-activity-declined")
        page.context.close()

    page = run.open(1280, 900)
    open_activity(page, run, "garbled-list-repo")
    load_activity(page)
    check("activity: a list that is not a list is reported as GitHub not giving a usable answer",
          "GitHub did not give a usable answer" in page.locator(".activity .failure").inner_text())  # fmt: skip
    page.context.close()

    page = run.open(375, 740)
    open_activity(page, run, "example-repo")
    load_activity(page)
    check("activity: on a phone nothing overflows sideways", not page.evaluate(OVERFLOW_X))
    columns = page.eval_on_selector(".activity-grid", "e => getComputedStyle(e).gridTemplateColumns.split(' ').length")
    check("activity: on a phone the two parts stack", columns == 1, columns)
    run.shot(page, "v30-repository-fixture-activity-phone")
    page.context.close()

    page = run.open(1280, 900, reduced_motion="reduce", forced_colors="active")
    open_activity(page, run, "example-repo")
    load_activity(page)
    check("activity: it works in forced-colors mode", count(page, ".people tbody tr") == 3)
    page.context.close()

    page = run.open(1280, 900)
    open_activity(page, run, "example-repo")
    page.get_by_role("button", name="Load contributors and latest release").click()
    page.fill("#repo-input", "fixture-org/quiet-repo")  # reading another repository while that one is still loading
    page.click("#repo-submit")
    page.wait_for_selector("#reading .report", timeout=20000)
    page.wait_for_function("document.getElementById('repo-submit').disabled === false", timeout=20000)
    page.wait_for_timeout(600)
    check("activity: a read that was still loading never paints into the next repository's report",
          "quiet-repo" in page.locator(".repo-name").inner_text() and count(page, ".people") == 0
          and count(page, ".activity .primary") == 1)  # fmt: skip
    page.context.close()

    # Last, because a rate limit leaves this server's quota budget spent and nothing after it could be read.
    page = run.open(1280, 900)
    open_activity(page, run, "burst-repo")
    load_activity(page)
    failure = page.locator(".activity .failure").inner_text()
    check("activity: a rate limit is explained inside the section",
          "GitHub's rate limit was reached" in failure, failure)  # fmt: skip
    check("activity: the repository reading above it is still there", count(page, ".report") == 1
          and "burst-repo" in page.locator(".repo-name").inner_text())  # fmt: skip
    check("activity: it offers to try again", page.locator(".activity .failure button").count() == 1)
    check("activity: the failure is announced as an alert",
          page.locator(".activity .failure").get_attribute("role") == "alert")  # fmt: skip
    check("activity: a failure leaves only the browser's own status messages in the console",
          only_http_noise(run.console(page)), run.console(page))  # fmt: skip
    run.shot(page, "v29-repository-fixture-activity-rate-limited")
    page.context.close()


def open_commit_activity(page: Any, run: Run, name: str) -> None:
    run.load(page, f"repository.html?repo=fixture-org/{name}", ready="#reading .report")


def stats_asks(standin: str, name: str = "example-repo") -> int:
    return sum(path.endswith(f"/{name}/stats/commit_activity") for path in standin_requests(standin))


def commit_activity(run: Run, standin: str) -> None:
    """The opt-in commit-activity section. FIXTURE DATA from the stand-in (made-up counts), never GitHub.

    The stand-in answers 202 with ``{}`` twice and then 200, the way GitHub really did on 2026-11-04.
    """
    check = run.check
    sys.path.insert(0, str(ROOT / "apps" / "api" / "tests"))
    from github_fakes import commit_activity_payload  # noqa: PLC0415

    year = commit_activity_payload()
    total = sum(week["total"] for week in year)
    peak = max(week["total"] for week in year)
    standin_reset(standin)
    page = run.open(1280, 900)
    open_commit_activity(page, run, "example-repo")
    section = page.locator(".commit-activity")
    quiet = standin_requests(standin)
    nothing_yet = count(page, ".commit-activity") == 1 and len(quiet) == 2 and count(page, ".commit-chart") == 0
    check("commits: the section is there but has asked GitHub for nothing", nothing_yet, quiet)
    people_button = page.get_by_role("button", name="Load contributors and latest release")
    check("commits: the contributors section is still exactly one, with its own button",
          count(page, ".activity") == 1 and people_button.count() == 1)
    prompt = page.locator(".commit-activity").inner_text()
    check("commits: it says what asking costs and that the first ask may not be answered",
          "once more, or twice" in prompt and "still computing" in prompt, prompt)  # fmt: skip
    described = page.locator(".commit-activity .primary").get_attribute("aria-describedby")
    check("commits: the cost note is tied to the button for a screen reader", described == "commit-activity-cost")

    page.focus(".commit-activity .primary")
    page.keyboard.press("Enter")  # the keyboard path, not a click
    page.wait_for_selector(".commit-activity .computing", timeout=20000)
    asked = standin_requests(standin)
    check("commits: pressing it asked for the commit activity and nothing else (the repository was reused)",
          asked[2:] == ["/repos/fixture-org/example-repo/stats/commit_activity"], asked)  # fmt: skip
    shown = page.locator(".commit-activity").inner_text()
    check("commits: a 202 from GitHub is shown as still working, not as an error or a chart",
          "GitHub is still working this out" in shown and count(page, ".commit-activity .failure") == 0
          and count(page, ".commit-chart") == 0, shown)  # fmt: skip
    check("commits: the keyboard focus is on the button that checks again", page.evaluate(
        "document.activeElement && document.activeElement.textContent") == "Check again")  # fmt: skip
    check("commits: a screen reader is told it is still computing",
          "still computing commit activity" in page.locator("#live").inner_text(), page.locator("#live").inner_text())
    page.wait_for_function("document.getElementById('quota').textContent.startsWith('57 of 60')", timeout=5000)
    check("commits: the quota line shows that even a 202 spent a request",
          "57 of 60" in page.locator("#quota").inner_text())  # fmt: skip
    run.shot(page, "v31-repository-fixture-commits-computing")
    page.wait_for_timeout(1500)
    check("commits: the page does not check again on its own", stats_asks(standin) == 1, stats_asks(standin))

    section.get_by_role("button", name="Check again").click()
    page.wait_for_selector(".commit-activity .computing", timeout=20000)
    page.wait_for_timeout(300)
    check("commits: checking again asked GitHub again, and it was still computing", stats_asks(standin) == 2)
    section.get_by_role("button", name="Check again").click()
    page.wait_for_selector(".commit-activity .commit-chart", timeout=20000)
    check("commits: the third ask got the year, one bar per week",
          count(page, ".commit-bar") == 52 and stats_asks(standin) == 3, count(page, ".commit-bar"))  # fmt: skip
    ledger = page.locator(".commit-activity .ledger").inner_text()
    check("commits: the total is added up from GitHub's weekly counts and labelled as a 52-week sum",
          "Commits in 52 weeks" in ledger and f"{total:,}" in ledger, ledger)  # fmt: skip
    check("commits: the busiest week is named with its count", f"{peak:,} commits, week of" in ledger, ledger)
    text = page.locator(".commit-activity").inner_text()
    check("commits: it says whose numbers are whose, and that the last bar is still filling",
          "added up by VYRON" in text and "last bar is the current week" in text
          and "says nothing about the quality" in text)  # fmt: skip
    check("commits: it carries a provenance badge and says the read was live",
          "Sourced" in text and page.locator(".commit-activity .cache-label").inner_text() == "Live")  # fmt: skip
    chart = page.locator(".commit-chart")
    label = chart.get_attribute("aria-label") or ""
    named = "52 weeks" in label and f"{total:,} commits" in label
    check("commits: the chart is one image with the figures in its name",
          chart.get_attribute("role") == "img" and named, label)
    first_title = page.locator(".commit-bar title").first.text_content()
    check("commits: every bar has a hover title with its week and count",
          count(page, ".commit-bar title") == 52 and first_title == f"Week of 9 Nov 2025: {year[0]['total']} commits")
    heights = page.locator(".commit-bar").evaluate_all("els => els.map(e => Number(e.getAttribute('height')))")
    weeks = [week["total"] for week in year]
    check("commits: a week with no commits has no bar and a week with commits has one",
          all((h == 0) == (t == 0) for h, t in zip(heights, weeks, strict=True)), heights)  # fmt: skip
    check("commits: the busiest week reaches the top", max(heights) == 99, max(heights))
    check("commits: nothing is drawn as text or an image inside the chart",
          count(page, ".commit-chart text") == 0 and count(page, ".commit-activity img") == 0)  # fmt: skip
    check("commits: at most one bar is marked as still filling", count(page, ".commit-bar.is-open") <= 1)
    axis = page.locator(".chart-axis").inner_text()
    both_ends = "Week of 9 Nov 2025" in axis and "Week of 1 Nov 2026" in axis
    check("commits: the axis names the first and last week", both_ends, axis)
    hrefs = page.locator(".commit-activity .evidence a").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    stats_url = "/repos/fixture-org/example-repo/stats/commit_activity"
    check("commits: the evidence is the two exact URLs it read",
          len(hrefs) == 2 and hrefs[1].endswith(stats_url), hrefs)
    check("commits: the table is there but closed", count(page, ".weeks-details[open]") == 0)
    page.locator(".weeks-details summary").click()
    check("commits: opened, the table has the same 52 weeks", count(page, ".weeks tbody tr") == 52
          and "9 Nov 2025" in page.locator(".weeks tbody tr").first.inner_text())  # fmt: skip
    check("commits: nothing overflows sideways", not page.evaluate(OVERFLOW_X))
    check("commits: the console is clean", not run.console(page), run.console(page))
    run.shot(page, "v32-repository-fixture-commits")
    before = standin_requests(standin)
    section.get_by_role("button", name="Check again").last.click()
    page.wait_for_selector(".commit-activity .commit-chart", timeout=20000)
    check("commits: checking again within a minute does not reach GitHub", standin_requests(standin) == before)
    page.context.close()

    page = run.open(1280, 900)
    open_commit_activity(page, run, "slow-stats-repo")
    section = page.locator(".commit-activity")
    for _ in range(3):
        section.get_by_role("button", name=re.compile("Load a year|Check again")).click()
        page.wait_for_selector(".commit-activity .computing", timeout=20000)
        page.wait_for_timeout(200)
    check("commits: a year GitHub never finishes stays 'still working' with a button, and never a chart",
          count(page, ".commit-chart") == 0 and section.get_by_role("button", name="Check again").count() == 1
          and stats_asks(standin, "slow-stats-repo") == 3)  # fmt: skip
    page.context.close()

    page = run.open(1280, 900)
    open_commit_activity(page, run, "quiet-repo")
    section = page.locator(".commit-activity")
    section.get_by_role("button", name="Load a year of commit activity").click()
    page.wait_for_selector(".commit-activity .dust", timeout=20000)
    shown = page.locator(".commit-activity").inner_text()
    check("commits: no commit activity is said in words, not drawn as an empty chart and not an error",
          "GitHub reports no commit activity for this repository." in shown
          and count(page, ".commit-chart") == 0 and count(page, ".commit-activity .failure") == 0, shown)  # fmt: skip
    page.context.close()

    standin_reset(standin)
    page = run.open(375, 740)
    open_commit_activity(page, run, "example-repo")
    section = page.locator(".commit-activity")
    for _ in range(3):
        section.get_by_role("button", name=re.compile("Load a year|Check again")).click()
        page.wait_for_selector(".commit-activity .computing, .commit-activity .commit-chart", timeout=20000)
        page.wait_for_timeout(200)
    check("commits: on a phone the chart is there and nothing overflows sideways",
          count(page, ".commit-bar") == 52 and not page.evaluate(OVERFLOW_X))  # fmt: skip
    box = page.locator(".commit-chart").bounding_box() or {"x": -1, "width": 0}
    check("commits: on a phone the chart fits inside the screen", box["x"] >= 0 and box["x"] + box["width"] <= 375, box)
    run.shot(page, "v33-repository-fixture-commits-phone")
    page.context.close()

    standin_reset(standin)
    page = run.open(1280, 900, reduced_motion="reduce", forced_colors="active")
    open_commit_activity(page, run, "example-repo")
    section = page.locator(".commit-activity")
    for _ in range(3):
        section.get_by_role("button", name=re.compile("Load a year|Check again")).click()
        page.wait_for_selector(".commit-activity .computing, .commit-activity .commit-chart", timeout=20000)
        page.wait_for_timeout(200)
    check("commits: it works in forced-colors mode", count(page, ".commit-bar") == 52)
    page.context.close()


def repository_switched_off(run: Run) -> None:
    """The server started with --no-github: the page must say so rather than look broken."""
    check = run.check
    page = run.open(1280, 900)
    run.load(page, "repository.html", ready="#reading .idle")
    analyze(page, "pallets/flask")
    title = page.locator(".failure-title").inner_text()
    check("repository: with GitHub switched off the page says so", title == "GitHub access is switched off", title)
    check("repository: it says how to switch it on", "--no-github" in page.locator(".failure").inner_text())
    check("repository: that is not offered as something to retry", count(page, ".failure button") == 0)
    check("repository: switched off leaves only the browser's own status messages", only_http_noise(run.console(page)))
    run.shot(page, "v26-repository-switched-off")
    page.context.close()


# EVERYTHING BELOW READS FIXTURE DATA FROM scripts/standin_registry.py, NOT PYPI OR NPM. Every package is called
# fixture-* (or sits in @fixture-scope) and says in its description that it is made up, so a screenshot taken here
# labels itself. A pass shows the page behaves correctly when a registry answers in the shapes the stand-in serves; it
# does not show that PyPI or npm send them (scripts/capture_registry.py records what they send).


def until(page: Any, expression: str, timeout: int = 20000) -> None:
    """Wait for a page expression to become true by asking again, because the page's policy forbids the eval that
    Playwright's own polling would use after the first look."""
    waited = 0
    while not page.evaluate(expression):
        if waited >= timeout:
            raise AssertionError(f"timed out waiting for: {expression}")
        page.wait_for_timeout(100)
        waited += 100


def registry_requests(standin: str) -> list[str]:
    with urllib.request.urlopen(standin + "/__standin/requests", timeout=5) as response:  # noqa: S310
        return list(json.load(response))


def node_sel(node_id: str) -> str:
    return f'.dep-node[data-id="{node_id}"]'


def read_package(page: Any, registry: str, text: str) -> None:
    page.select_option("#dep-registry", registry)
    page.fill("#dep-input", text)
    page.click("#dep-submit")
    page.wait_for_selector("#reading .dep-node, #reading .failure", timeout=20000)
    until(page, "document.getElementById('dep-submit').disabled === false", timeout=20000)


def open_node(page: Any, node_id: str, status: str = "read") -> None:
    page.click(node_sel(node_id))
    page.wait_for_selector(f'{node_sel(node_id)}[data-status="{status}"]', timeout=20000)
    until(page, "document.querySelector('#reading [aria-busy=true]') === null", timeout=20000)


def status_of(page: Any, node_id: str) -> str:
    return page.locator(node_sel(node_id)).get_attribute("data-status") or ""


def node_ids(page: Any) -> list[str]:
    return page.locator(".dep-node").evaluate_all("nodes => nodes.map(n => n.getAttribute('data-id'))")


def graph_line(page: Any) -> str:
    return page.locator(".graph-summary").inner_text()


def active_node(page: Any) -> str:
    return page.evaluate("document.activeElement ? (document.activeElement.getAttribute('data-id') || '') : ''")


def live_text(page: Any) -> str:
    return page.locator("#live").inner_text()


def box_x(page: Any, node_id: str) -> float:
    box = page.locator(node_sel(node_id) + " .node-box").bounding_box()
    return float(box["x"]) if box else -1.0


def dependencies_page(run: Run, standin: str) -> None:
    check = run.check
    page = run.open(1280, 900)
    run.load(page, "dependencies.html", ready="#reading .idle")
    check(
        "dependencies: it opens on an invitation",
        page.locator("#reading-title").inner_text() == "Read a package's dependencies",
    )
    check("dependencies: it offers one example for each registry", count(page, ".example") == 2)
    check("dependencies: opening it has not asked a registry for anything", registry_requests(standin) == [])
    check("dependencies: the page does not overflow sideways", not page.evaluate(OVERFLOW_X))
    check("dependencies: the console is clean when idle", not run.console(page), run.console(page))
    check(
        "dependencies: the other two pages are linked",
        [a.get_attribute("href") for a in page.locator(".bar .page-link").all()] == ["/", "/repository.html"],
    )
    run.shot(page, "v38-dependencies-idle")

    # -- a package and what it lists
    read_package(page, "pypi", "fixture-web")
    web, router, templates = "pypi:fixture-web", "pypi:fixture-router", "pypi:fixture-templates"
    utils, direct, markup = "pypi:fixture-utils", "pypi:fixture-direct", "pypi:fixture-markup"
    check(
        "dependencies: reading a package asked the registry once",
        registry_requests(standin) == ["/pypi-host/pypi/fixture-web/json"],
        registry_requests(standin),
    )
    check(
        "dependencies: the root and its four dependencies are drawn",
        node_ids(page) == [web, direct, router, templates, utils],
        node_ids(page),
    )
    check(
        "dependencies: the root is read, the others are not",
        [status_of(page, n) for n in (web, router, templates, utils, direct)]
        == ["read", "unread", "unread", "unread", "unreadable"],
    )
    check(
        "dependencies: the graph says how much of it is still unopened",
        graph_line(page) == "5 packages and 4 dependency links, 3 not opened yet.",
        graph_line(page),
    )
    check(
        "dependencies: the address names the package",
        page.url.endswith("?package=pypi:fixture-web") or page.url.endswith("?package=pypi%3Afixture-web"),
        page.url,
    )
    check(
        "dependencies: focus went to the package's name", page.evaluate("document.activeElement.id") == "reading-title"
    )
    check(
        "dependencies: the root is selected and its details are beside the graph",
        page.locator("#details-title").inner_text() == "fixture-web",
    )
    check(
        "dependencies: the page says the answer is a stand-in's, not PyPI's",
        "stand-in registry" in page.locator(".provenance-band").inner_text(),
    )
    check(
        "dependencies: it carries the provenance label",
        page.locator(".provenance-band .badge").inner_text().strip() == "Sourced",
    )
    check(
        "dependencies: no link to a registry page is made up for a stand-in",
        count(page, ".dep-details a.text-link") == 0,
    )
    details = page.locator(".dep-details").inner_text()
    check(
        "dependencies: what the package left out is said in words",
        "2 requirements that apply only with an extra or on certain systems." in details,
        details,
    )
    check(
        "dependencies: the evidence is the address that was read",
        page.locator(".dep-details .evidence a").get_attribute("href").endswith("/pypi-host/pypi/fixture-web/json"),
    )
    check(
        "dependencies: how old the answer is is said",
        re.search(r"Read from PyPI (just now|\d+ seconds? ago)\.", details) is not None,
        details,
    )
    check(
        "dependencies: a link that is not a name is drawn dashed and called unreadable",
        page.locator('.dep-node[data-id="pypi:fixture-direct"] .node-box').evaluate(
            "n => getComputedStyle(n).strokeDasharray"
        )
        != "none",
    )
    check("dependencies: an unreadable package offers no read button", count(page, "#open-package") == 0)
    check(
        "dependencies: the legend names the three states",
        page.locator(".graph-legend").inner_text().split("\n")
        == ["Dependencies read", "Not opened yet", "Cannot be opened"],
    )
    check("dependencies: the page does not overflow sideways with a graph", not page.evaluate(OVERFLOW_X))
    run.shot(page, "v39-dependencies-root")

    # -- growing it, one package per request
    open_node(page, router)
    check(
        "dependencies: opening a dependency asked the registry for exactly that package",
        registry_requests(standin)[-1] == "/pypi-host/pypi/fixture-router/json"
        and len(registry_requests(standin)) == 2,
    )
    check(
        "dependencies: what it needs was already in the graph, and the page said so",
        "fixture-router lists 1 dependency; none are new to the graph." in live_text(page),
        live_text(page),
    )
    check(
        "dependencies: the shared package moved to the right of both its dependents",
        box_x(page, utils) > box_x(page, router),
    )
    open_node(page, templates)
    check(
        "dependencies: a second dependency grew the graph by the one package it adds",
        node_ids(page)[-1] == markup and graph_line(page) == "6 packages and 7 dependency links, 2 not opened yet.",
        graph_line(page),
    )
    check(
        "dependencies: and the page said so",
        "fixture-templates lists 2 dependencies; 1 is new to the graph." in live_text(page),
        live_text(page),
    )
    check(
        "dependencies: the newest column is brought into view",
        page.locator(node_sel(markup)).evaluate(
            "n => { const r = n.getBoundingClientRect(), c = n.closest('.graph-scroll').getBoundingClientRect();"
            " return r.right <= c.right + 1 && r.left >= c.left - 1; }"
        ),
    )
    check(
        "dependencies: the node count shows what each opened package lists",
        [page.locator(node_sel(n) + " .node-count").text_content() for n in (web, router, templates)]
        == ["4", "1", "2"],
    )
    check(
        "dependencies: no node is a dead end in the tab order: exactly one is tabbable",
        count(page, '.dep-node[tabindex="0"]') == 1,
    )
    run.shot(page, "v40-dependencies-grown")

    before = registry_requests(standin)
    page.click(node_sel(web))
    page.click(node_sel(direct))
    check(
        "dependencies: selecting an opened package, or one that cannot be opened, asks nothing",
        registry_requests(standin) == before,
    )
    check(
        "dependencies: selecting one shows its details",
        page.locator("#details-title").inner_text() == "fixture-direct"
        and "Cannot be opened" in page.locator(".details-status").inner_text(),
    )
    check(
        "dependencies: the selected node is marked for assistive technology",
        page.locator(node_sel(direct)).get_attribute("aria-current") == "true"
        and count(page, '.dep-node[aria-current="true"]') == 1,
    )
    page.click(node_sel(web))
    check(
        "dependencies: a package that lists two others names each beside its requirement",
        page.locator(".dep-details .dep-list li").first.inner_text().strip()
        == "fixture-direct given as a link or path",
    )

    # -- keyboard: arrows move, Enter opens, Home goes back to the start
    page.locator(node_sel(web)).focus()
    page.keyboard.press("ArrowRight")
    first = active_node(page)
    check(
        "dependencies: the right arrow goes to a dependency of the focused package",
        first in (direct, router, templates, utils),
        first,
    )
    check(
        "dependencies: moving focus did not select or read anything",
        registry_requests(standin) == before and page.locator("#details-title").inner_text() == "fixture-web",
    )
    page.locator(node_sel(templates)).focus()
    page.keyboard.press("ArrowRight")
    check(
        "dependencies: the arrow keys follow the lines, so templates goes right to its own dependency",
        active_node(page) in (utils, markup),
        active_node(page),
    )
    page.locator(node_sel(utils)).focus()
    page.keyboard.press("ArrowLeft")
    check(
        "dependencies: left goes back to a package that needs it",
        active_node(page) in (router, templates),
        active_node(page),
    )
    page.keyboard.press("Home")
    check("dependencies: Home goes to the starting package", active_node(page) == web)
    page.locator(node_sel(utils)).focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(f'{node_sel(utils)}[data-status="read"]')
    check(
        "dependencies: Enter opens the focused package and keeps focus on it",
        active_node(page) == utils and "fixture-utils lists no dependencies." in live_text(page),
        live_text(page),
    )
    check(
        "dependencies: the root's name in a read-out says it starts the graph",
        "Starting package" in page.locator(node_sel(web)).get_attribute("aria-label"),
    )
    page.locator(node_sel(markup)).focus()
    page.keyboard.press("Space")
    page.wait_for_selector(f'{node_sel(markup)}[data-status="read"]')
    check("dependencies: Space opens it too", status_of(page, markup) == "read")
    check(
        "dependencies: when everything that can be opened has been, the graph says so",
        graph_line(page) == "6 packages and 7 dependency links, every package that can be opened has been.",
        graph_line(page),
    )

    # -- the text alternative
    check("dependencies: the outline starts closed", not page.locator(".dep-outline").evaluate("d => d.open"))
    page.click(".dep-outline summary")
    rows = page.locator(".outline-list li").count()
    outline = page.locator(".dep-outline").inner_text()
    check(
        "dependencies: the outline lists each package where it first appears and says when it repeats",
        "listed above, so its dependencies are not listed again" in outline and "starting package" in outline,
        outline[:300],
    )
    check("dependencies: the outline has a row per link and one for the root", rows == 8, rows)
    page.click(".outline-list .outline-link >> text=fixture-markup")
    check(
        "dependencies: choosing a package in the outline selects it and moves focus to its details",
        page.locator("#details-title").inner_text() == "fixture-markup"
        and page.evaluate("document.activeElement.id") == "details-title",
    )
    page.click(".dep-details .dep-list .outline-link >> nth=0")
    check(
        "dependencies: the lists in the details do the same",
        page.evaluate("document.activeElement.id") == "details-title",
    )
    run.shot(page, "v41-dependencies-outline")
    check("dependencies: nothing unexpected reached the console", not run.console(page), run.console(page))

    # -- a second look is not a second request
    before = registry_requests(standin)
    page.goto(run.base + "dependencies.html?package=pypi:fixture-web")
    page.wait_for_selector("#reading .dep-node")
    check(
        "dependencies: reading the same package again inside ten minutes did not reach the registry",
        registry_requests(standin) == before,
    )
    check(
        "dependencies: the address restores the starting package alone, not what was opened",
        count(page, ".dep-node") == 5,
    )
    page.go_back()
    page.wait_for_selector("#reading .idle, #reading .dep-node")
    page.context.close()


def dependencies_shapes(run: Run, standin: str) -> None:
    check = run.check
    page = run.open(1280, 900)
    run.load(page, "dependencies.html", ready="#reading .idle")

    read_package(page, "pypi", "fixture-cycle-a")
    open_node(page, "pypi:fixture-cycle-b")
    check(
        "dependencies: a cycle is drawn once, with both lines",
        node_ids(page) == ["pypi:fixture-cycle-a", "pypi:fixture-cycle-b"] and count(page, ".dep-edge") == 2,
    )
    check(
        "dependencies: and the graph says nothing is left to open",
        graph_line(page) == "2 packages and 2 dependency links, every package that can be opened has been.",
        graph_line(page),
    )
    check(
        "dependencies: the cycle does not run past the second column",
        box_x(page, "pypi:fixture-cycle-b") > box_x(page, "pypi:fixture-cycle-a") and count(page, ".dep-svg") == 1,
    )
    page.click(".dep-outline summary")
    check("dependencies: the outline ends at the cycle", "listed above" in page.locator(".dep-outline").inner_text())

    read_package(page, "pypi", "FIXTURE_Wide")
    check(
        "dependencies: a package with forty dependencies draws them all, in one column",
        count(page, ".dep-node") == 41
        and graph_line(page) == "41 packages and 40 dependency links, 40 not opened yet.",
        graph_line(page),
    )
    check(
        "dependencies: a name spelled another way is filed under the registry's spelling",
        page.locator("#details-title").inner_text() == "fixture-wide"
        and page.locator("#dep-input").input_value() == "fixture-wide",
    )
    check(
        "dependencies: the drawing scrolls inside its own box instead of stretching the page",
        page.locator(".graph-scroll").evaluate("n => n.scrollHeight > n.clientHeight")
        and not page.evaluate(OVERFLOW_X),
    )
    page.click(node_sel("pypi:fixture-wide-part-00"))
    page.wait_for_selector(".dep-details .failure")
    problem = page.locator(".dep-details .failure").inner_text()
    check(
        "dependencies: a dependency the registry has never heard of says so, and names it",
        "No package called fixture-wide-part-00 on PyPI" in problem,
        problem,
    )
    check(
        "dependencies: its details say the last attempt failed",
        "The last attempt to open it failed." in page.locator(".details-status").inner_text(),
    )
    check(
        "dependencies: the graph stays and the package stays unopened",
        count(page, ".dep-node") == 41 and status_of(page, "pypi:fixture-wide-part-00") == "unread",
    )
    check(
        "dependencies: a package that does not exist is not offered a retry",
        count(page, ".dep-details .failure button") == 0 and count(page, "#open-package") == 0,
    )
    check("dependencies: the failure was announced", "could not be opened" in live_text(page), live_text(page))
    run.shot(page, "v42-dependencies-wide")

    # -- a package whose dependencies fail in the two retryable ways
    read_package(page, "pypi", "fixture-fragile")
    page.click(node_sel("pypi:fixture-broken"))
    page.wait_for_selector(".dep-details .failure")
    problem = page.locator(".dep-details .failure").inner_text()
    check(
        "dependencies: a registry error on opening a dependency is explained in its details",
        "PyPI did not give a usable answer" in problem
        and page.locator("#details-title").inner_text() == "fixture-broken",
        problem,
    )
    check("dependencies: and a retry is offered", count(page, ".dep-details .failure button") == 1)
    before = len(registry_requests(standin))
    page.click(".dep-details .failure button")
    until(page, "document.querySelector('#reading [aria-busy=true]') === null")
    page.wait_for_selector(".dep-details .failure")
    check("dependencies: trying again asked the registry again", len(registry_requests(standin)) == before + 1)
    page.click(node_sel("pypi:fixture-limited"))
    page.wait_for_selector(".dep-details .failure")
    until(page, "document.getElementById('details-title').innerText === 'fixture-limited'")
    problem = page.locator(".dep-details .failure").inner_text()
    check(
        "dependencies: a rate limit says whose, and what to do",
        "PyPI is limiting requests" in problem
        and "Wait 1 minute." in problem
        and count(page, ".dep-details .failure button") == 1,
        problem,
    )
    run.shot(page, "v43-dependencies-expansion-failure")
    check(
        "dependencies: those failures left only the browser's own status messages",
        only_http_noise(run.console(page)),
        run.console(page),
    )
    page.context.close()


def dependencies_failures(run: Run) -> None:
    check = run.check
    page = run.open(1280, 900)
    run.load(page, "dependencies.html", ready="#reading .idle")
    cases = {
        "fixture-missing": ("No package called fixture-missing on PyPI", False),
        "fixture-limited": ("PyPI is limiting requests", True),
        "fixture-broken": ("PyPI did not give a usable answer", True),
        "fixture-garbled": ("PyPI did not give a usable answer", True),
    }
    for name, (title, retry) in cases.items():
        read_package(page, "pypi", name)
        got = page.locator(".failure-title").inner_text()
        check(f"dependencies: {name} says what happened", got == title, got)
        check(
            f"dependencies: {name} {'offers' if retry else 'does not offer'} a retry",
            (count(page, ".failure button") == 1) == retry,
        )
        check(
            f"dependencies: {name} moved focus to the explanation",
            page.evaluate("document.activeElement.id") == "reading-title",
        )
    read_package(page, "pypi", "fixture-limited")
    check(
        "dependencies: a rate limit says how long to wait",
        "Wait 1 minute." in page.locator(".failure").inner_text(),
        page.locator(".failure").inner_text(),
    )
    run.shot(page, "v44-dependencies-limited")
    read_package(page, "npm", "fixture-missing")
    check(
        "dependencies: a missing npm package names npm",
        page.locator(".failure-title").inner_text() == "No package called fixture-missing on npm",
    )
    for text in ("two words", "pypi:", "https://example.com/x", "x" * 215, ""):
        page.select_option("#dep-registry", "pypi")
        page.fill("#dep-input", text)
        page.click("#dep-submit")
        page.wait_for_selector(".failure")
        check(
            f"dependencies: {text[:20]!r} is explained without asking a registry",
            page.locator(".failure-title").inner_text() == "That is not a package name",
            page.locator(".failure").inner_text(),
        )
    check(
        "dependencies: failures leave only the browser's own status messages",
        only_http_noise(run.console(page)),
        run.console(page),
    )
    page.context.close()


def dependencies_npm(run: Run, standin: str) -> None:
    check = run.check
    page = run.open(1280, 900)
    run.load(page, "dependencies.html", ready="#reading .idle")
    page.fill("#dep-input", "https://www.npmjs.com/package/fixture-server")
    page.click("#dep-submit")
    page.wait_for_selector("#reading .dep-node")
    check(
        "dependencies: a pasted npm link chooses npm over the picker",
        page.locator("#dep-registry").input_value() == "npm"
        and page.locator("#details-title").inner_text() == "fixture-server",
    )
    check(
        "dependencies: an alias and a git dependency cannot be opened",
        [status_of(page, f"npm:{n}") for n in ("fixture-alias", "fixture-gitdep")] == ["unreadable", "unreadable"],
    )
    check(
        "dependencies: the other three can",
        [status_of(page, f"npm:{n}") for n in ("fixture-body", "fixture-cookie", "@fixture-scope/types")]
        == ["unread"] * 3,
    )
    details = page.locator(".dep-details").inner_text()
    check(
        "dependencies: development and peer dependencies are named, not drawn",
        "1 development dependency" in details and "1 peer dependency" in details and "fixture-test" not in details,
        details,
    )
    open_node(page, "npm:@fixture-scope/types")
    check(
        "dependencies: a scoped name is asked for whole",
        registry_requests(standin)[-1] == "/npm-host/@fixture-scope/types",
        registry_requests(standin),
    )
    check("dependencies: npm answers are labelled npm", "Read from npm" in page.locator(".dep-details").inner_text())
    run.shot(page, "v45-dependencies-npm")
    page.goto(run.base + "dependencies.html?package=npm:@fixture-scope/types")
    page.wait_for_selector("#reading .dep-node")
    check(
        "dependencies: a scoped name survives the address",
        page.locator("#details-title").inner_text() == "@fixture-scope/types",
    )
    page.context.close()


def dependencies_small(run: Run) -> None:
    check = run.check
    for width, height, name in ((375, 667, "v46-dependencies-phone"), (320, 568, "")):
        page = run.open(width, height, has_touch=True, is_mobile=True)
        run.load(page, "dependencies.html?package=pypi:fixture-web", ready="#reading .dep-node")
        check(
            f"dependencies: {width}px: no horizontal overflow",
            not page.evaluate(OVERFLOW_X),
            page.evaluate("document.documentElement.scrollWidth"),
        )
        check(
            f"dependencies: {width}px: the form fits its row",
            page.evaluate(
                "(() => { const f = document.getElementById('dep-form').getBoundingClientRect();"
                " return f.right <= innerWidth && f.left >= 0; })()"
            ),
        )
        page.tap(node_sel("pypi:fixture-router"))
        page.wait_for_selector(f'{node_sel("pypi:fixture-router")}[data-status="read"]')
        check(
            f"dependencies: {width}px: tapping a package opens it",
            status_of(page, "pypi:fixture-router") == "read"
            and page.locator("#details-title").inner_text() == "fixture-router",
        )
        check(
            f"dependencies: {width}px: the details sit under the drawing",
            page.evaluate(
                "document.querySelector('.dep-details').getBoundingClientRect().top"
                " >= document.querySelector('.graph-scroll').getBoundingClientRect().bottom - 1"
            ),
        )
        check(
            f"dependencies: {width}px: the drawing scrolls sideways inside its box, not the page",
            page.locator(".graph-scroll").evaluate("n => n.scrollWidth > n.clientWidth")
            and not page.evaluate(OVERFLOW_X),
        )
        small = page.evaluate(
            "[...document.querySelectorAll('.dep-details button, .dep-details .outline-link')]"
            ".filter(b => b.getBoundingClientRect().height < 24).length"
        )
        check(f"dependencies: {width}px: controls in the details are at least 24px tall", small == 0, small)
        if name:
            page.evaluate("document.getElementById('dep-main').scrollTo(0, 320)")
            run.shot(page, name)
        check(f"dependencies: {width}px: the console is clean", not run.console(page), run.console(page))
        page.context.close()


def release_marks(page: Any) -> int:
    return count(page, ".release-mark")


def selected_release(page: Any) -> str:
    return page.evaluate(
        "(() => { const m = document.querySelector('.release-mark.is-selected');"
        " return m ? m.getAttribute('data-index') : ''; })()"
    )


def focused_release(page: Any) -> str:
    return page.evaluate(
        "(() => { const a = document.activeElement; return a && a.classList.contains('release-mark')"
        " ? a.getAttribute('data-index') : ''; })()"
    )


def release_readout(page: Any) -> str:
    return page.locator("#release-readout").inner_text()


def show_history(page: Any) -> None:
    page.click("#release-load")
    page.wait_for_selector("#reading .release-svg, #reading .release-host .failure", timeout=20000)
    until(page, "document.getElementById('release-title') === document.activeElement", timeout=20000)


def dependencies_releases(run: Run, standin: str) -> None:
    """The release history, from FIXTURE release dates served by the stand-in registry (never PyPI or npm)."""
    check = run.check
    page = run.open(1280, 900)
    release_calls: list[str] = []
    page.on(
        "request",
        lambda r: release_calls.append(r.url) if "/api/v1/registries/" in r.url and "/releases?" in r.url else None,
    )
    run.load(page, "dependencies.html?package=pypi:fixture-web", ready="#reading .dep-node")
    asked = len(registry_requests(standin))
    check(
        "releases: the section names the selected package and offers to load",
        page.locator("#release-title").inner_text() == "Release history of fixture-web"
        and page.locator("#release-load").inner_text() == "Show release history for fixture-web",
    )
    check("releases: nothing is asked for until the button is pressed", release_calls == [], release_calls)
    check(
        "releases: it says that VYRON's copy is reused, without promising it",
        "If VYRON still holds that answer from the last ten minutes" in page.locator(".release-lede").inner_text(),
    )
    show_history(page)
    check(
        "releases: the history came from the answer VYRON already had, so the registry was not asked again",
        len(registry_requests(standin)) == asked,
        registry_requests(standin),
    )
    check("releases: one API request was made, for the releases route", len(release_calls) == 1, release_calls)
    check(
        "releases: focus moved to the section's heading",
        page.evaluate("document.activeElement.id") == "release-title",
    )
    body = page.locator(".release-body").inner_text()
    check(
        "releases: the summary counts, dates and flags in words",
        "23 releases, from 12 March 2014 to 17 April 2025. 4 are pre-releases by version number. 1 is yanked." in body,
        body[:400],
    )
    check(
        "releases: it says what a date is, and that it is not the announcement day",
        "earliest time PyPI recorded a file being uploaded" in body
        and "not necessarily the day its author tagged or announced it" in body,
    )
    check(
        "releases: the version with no files is counted, not listed",
        "1 version has no usable date in the registry's answer, so it is not on the strip." in body,
    )
    check(
        "releases: it says the answer is a stand-in's and carries the provenance label",
        "stand-in registry" in page.locator(".release-body .stand-in").inner_text()
        and page.locator(".release-body .badge").inner_text().strip() == "Sourced",
    )
    check(
        "releases: how old the answer is is said",
        re.search(r"Read from PyPI (just now|\d+ seconds? ago)\.", body) is not None,
    )
    check("releases: one mark per release", release_marks(page) == 23, release_marks(page))
    check(
        "releases: a yanked version is a diamond, four pre-releases are hollow",
        count(page, '.release-mark[data-shape="diamond"]') == 1
        and count(page, '.release-mark[data-hollow="true"]') == 4,
    )
    check(
        "releases: the legend names the three marks",
        page.locator(".release-body .graph-legend").inner_text().split("\n")
        == ["Release", "Pre-release by its version number", "Yanked or deprecated"],
    )
    labels = page.locator(".release-tick-label").evaluate_all("nodes => nodes.map(n => n.textContent)")
    check(
        "releases: the year axis labels every year of a twelve-year history",
        labels == [str(y) for y in range(2014, 2027)],
        labels,
    )
    check(
        "releases: the newest release is selected and described in full",
        selected_release(page) == "22"
        and release_readout(page) == "Version 2.1.0, 17 April 2025, 10:15 UTC."
        and page.locator(".release-position").inner_text() == "Release 23 of 23",
        release_readout(page),
    )
    check("releases: exactly one mark is tabbable", count(page, '.release-mark[tabindex="0"]') == 1)
    check(
        "releases: each mark is labelled for assistive technology",
        page.locator('.release-mark[data-index="16"]').get_attribute("aria-label")
        == "1.5.1, 15 February 2022, yanked",
        page.locator('.release-mark[data-index="16"]').get_attribute("aria-label"),
    )
    check("releases: the strip does not widen the page", not page.evaluate(OVERFLOW_X))
    page.locator(".release-block").evaluate("n => n.scrollIntoView({block: 'start'})")
    run.shot(page, "v48-dependencies-releases")

    # -- keyboard: one tab stop, arrows move between releases
    page.locator(".release-mark.is-selected").focus()
    page.keyboard.press("ArrowLeft")
    check(
        "releases: Left moves to the earlier release and takes focus with it",
        selected_release(page) == "21" and focused_release(page) == "21"
        and release_readout(page) == "Version 2.0.0, 6 February 2024, 10:15 UTC.",
        release_readout(page),
    )
    page.keyboard.press("Home")
    check(
        "releases: Home goes to the first",
        selected_release(page) == "0" and release_readout(page) == "Version 0.1.0, 12 March 2014, 10:15 UTC.",
    )
    page.keyboard.press("ArrowLeft")
    check("releases: it stops at the first", selected_release(page) == "0")
    page.keyboard.press("End")
    page.keyboard.press("PageUp")
    check("releases: PageUp moves ten", selected_release(page) == "12", selected_release(page))
    page.keyboard.press("Tab")
    check(
        "releases: Tab leaves the strip (it is one tab stop)",
        focused_release(page) == "" and page.evaluate("document.activeElement.id") == "release-earlier",
        page.evaluate("document.activeElement.id"),
    )

    # -- the pointer, and what yanked says
    page.locator('.release-mark[data-index="16"]').click()
    text = release_readout(page)
    check(
        "releases: clicking a mark selects it, and a yanked one says what yanked means",
        selected_release(page) == "16"
        and text.startswith(
            "Version 1.5.1, 15 February 2022, 10:15 UTC. Yanked: PyPI marks every file of this version"
        ),
        text,
    )
    page.locator('.release-mark[data-index="6"]').click()
    check(
        "releases: a pre-release says its status is read from the number",
        "Pre-release by its version number. That is a reading of the number, not something the registry says."
        in release_readout(page),
        release_readout(page),
    )

    # -- the two buttons
    page.click("#release-later")
    check(
        "releases: the Later button moves one on, keeps focus on itself and says so in the live region",
        selected_release(page) == "7" and page.evaluate("document.activeElement.id") == "release-later"
        and live_text(page).startswith("Release 8 of 23."),
        live_text(page),
    )
    page.locator('.release-mark[data-index="0"]').click()
    check(
        "releases: at the first release the Earlier button is marked unavailable, not removed",
        page.locator("#release-earlier").get_attribute("aria-disabled") == "true"
        and page.locator("#release-later").get_attribute("aria-disabled") == "false",
    )
    # Playwright will not click an aria-disabled button by default; a person can, so force it.
    page.click("#release-earlier", force=True)
    check("releases: pressing an unavailable button changes nothing", selected_release(page) == "0")

    # -- the table
    check("releases: the table starts closed", not page.locator(".release-list").evaluate("n => n.open"))
    page.click(".release-list summary")
    rows = page.locator(".release-row")
    check(
        "releases: the table lists every release, newest first",
        rows.count() == 23
        and rows.first.locator("th").inner_text() == "2.1.0"
        and rows.last.locator("th").inner_text() == "0.1.0",
        rows.count(),
    )
    check(
        "releases: it needs no 'show all' when it is short",
        count(page, "#release-more") == 0,
    )
    check(
        "releases: the selected release's row is marked in the table",
        page.locator(".release-row.is-current th").inner_text() == "0.1.0"
        and page.locator(".release-row.is-current").get_attribute("aria-current") == "true",
    )
    notes = {
        r.locator("th").inner_text(): r.locator("td").nth(1).inner_text() for r in rows.all()
    }
    check(
        "releases: the table's notes say pre-release and yanked in words",
        notes["1.0.0rc1"] == "pre-release by number" and notes["1.5.1"] == "yanked" and notes["1.0.0"] == "",
        notes,
    )

    # -- what is kept when another package is looked at
    page.click(node_sel("pypi:fixture-router"))
    until(page, "document.querySelector('#reading [aria-busy=true]') === null", timeout=20000)
    check(
        "releases: another package starts with its own invitation, not the first one's history",
        page.locator("#release-title").inner_text() == "Release history of fixture-router"
        and count(page, "#release-load") == 1 and release_marks(page) == 0,
    )
    page.click(node_sel("pypi:fixture-web"))
    check(
        "releases: going back finds the history, the chosen release and the open table as they were",
        release_marks(page) == 23 and selected_release(page) == "0"
        and page.locator(".release-list").evaluate("n => n.open"),
    )
    check("releases: none of that went to the API again", len(release_calls) == 1, release_calls)
    check("releases: the console is clean", not run.console(page), run.console(page))
    page.context.close()

    # -- npm: publish times, a deprecated version, an unpublished one
    page = run.open(1280, 900)
    run.load(page, "dependencies.html?package=npm:fixture-server", ready="#reading .dep-node")
    show_history(page)
    body = page.locator(".release-body").inner_text()
    check(
        "releases: npm's summary counts pre-releases and the deprecated version",
        "11 releases, from 1 June 2012 to 8 March 2024. 2 are pre-releases by version number. 1 is deprecated." in body,
        body[:300],
    )
    check(
        "releases: npm's date is called a publish time",
        "publish time npm recorded for that version" in body,
    )
    check(
        "releases: the version npm no longer publishes is counted as removed, not listed",
        "1 version has a recorded time but is no longer published on npm, so it is not listed." in body
        and release_marks(page) == 11,
    )
    check(
        "releases: the deprecated version is a diamond and says what that means",
        count(page, '.release-mark[data-shape="diamond"]') == 1,
    )
    page.locator('.release-mark[data-status="deprecated"]').click()
    check(
        "releases: it says who deprecated it, in npm's terms",
        "Deprecated: the maintainers attached a deprecation notice to this version on npm." in release_readout(page),
        release_readout(page),
    )
    page.context.close()


def dependencies_release_wide(run: Run, standin: str) -> None:
    """A history of 312 releases with a burst of 40 inside one day: overlap is counted and said, the table is cut."""
    check = run.check
    page = run.open(1280, 900)
    run.load(page, "dependencies.html?package=pypi:fixture-wide", ready="#reading .dep-node")
    show_history(page)
    body = page.locator(".release-body").inner_text()
    check("releases: wide: 312 marks", release_marks(page) == 312, release_marks(page))
    check(
        "releases: wide: marks that cannot be told apart are counted and said, with where the full list is",
        re.search(r"\d+ marks overlap another, because those releases are too close together to tell apart", body)
        is not None
        and "The table lists every release." in body,
        body[:600],
    )
    check(
        "releases: wide: the drawing gets wider with a longer history",
        page.locator(".release-svg").evaluate("n => n.getBoundingClientRect().width") >= 1000,
    )
    page.locator(".release-mark.is-selected").focus()
    page.keyboard.press("Home")
    page.keyboard.press("End")
    check("releases: wide: keys still walk a long history", selected_release(page) == "311", selected_release(page))
    page.click(".release-list summary")
    check(
        "releases: wide: only the newest 100 rows are shown, and the button says how many there are",
        count(page, ".release-row") == 100 and page.locator("#release-more").inner_text() == "Show all 312 releases",
    )
    page.click("#release-more")
    check(
        "releases: wide: showing all keeps the table open and focus on the button",
        count(page, ".release-row") == 312
        and page.locator("#release-more").inner_text() == "Show only the newest 100"
        and page.evaluate("document.activeElement.id") == "release-more"
        and page.locator(".release-list").evaluate("n => n.open"),
    )
    check("releases: wide: the page does not overflow sideways", not page.evaluate(OVERFLOW_X))
    page.evaluate("document.getElementById('dep-main').scrollTo(0, 0)")
    page.locator(".release-scroll").scroll_into_view_if_needed()
    run.shot(page, "v49-dependencies-releases-wide")
    check("releases: wide: the console is clean", not run.console(page), run.console(page))
    page.context.close()


def dependencies_release_failures(run: Run, standin: str) -> None:
    """A registry that sends no dates, one that is limiting requests, one that is broken, a link that is no name."""
    check = run.check
    page = run.open(1280, 900)
    run.load(page, "dependencies.html?package=pypi:fixture-undated", ready="#reading .dep-node")
    show_history(page)
    failure = page.locator(".release-host .failure")
    check(
        "releases: no dates from the registry is said in the registry's terms, with no retry offered",
        failure.locator(".failure-title").inner_text() == "PyPI sent no release dates for fixture-undated"
        and "PyPI documents that list as deprecated" in failure.inner_text()
        and count(page, "#release-retry") == 0,
        failure.inner_text(),
    )
    check(
        "releases: and says the dependencies are not affected, which they are not",
        "dependencies on this page are not affected" in failure.inner_text()
        and page.locator("#details-title").inner_text() == "fixture-undated"
        and count(page, ".dep-details .evidence a") == 1,
    )
    check("releases: the failure was announced", "sent no release dates" in live_text(page), live_text(page))
    page.context.close()

    page = run.open(1280, 900)
    run.load(page, "dependencies.html?package=pypi:fixture-fragile", ready="#reading .dep-node")
    for name, title, retry in (
        ("fixture-limited", "PyPI is limiting requests", True),
        ("fixture-broken", "PyPI did not give a usable answer", True),
    ):
        page.click(node_sel(f"pypi:{name}"))
        until(page, "document.querySelector('#reading [aria-busy=true]') === null", timeout=20000)
        page.wait_for_selector(".dep-details .failure", timeout=20000)
        show_history(page)
        shown = page.locator(".release-host .failure .failure-title").inner_text()
        check(f"releases: {name}: the failure names the registry's trouble", shown == title, shown)
        check(
            f"releases: {name}: a retry is offered",
            (count(page, "#release-retry") == 1) == retry,
        )
        check(
            f"releases: {name}: the failure is announced and keeps the rest of the page working",
            title in live_text(page) and count(page, ".dep-node") >= 3,
            live_text(page),
        )
    page.click("#release-retry")
    page.wait_for_selector(".release-host .failure", timeout=20000)
    check("releases: trying again asks again and can fail again", count(page, ".release-host .failure") == 1)
    page.click(node_sel("pypi:fixture-limited"))
    page.wait_for_selector("#release-retry", timeout=20000)
    check(
        "releases: each package keeps its own failure",
        page.locator(".release-host .failure .failure-title").inner_text() == "PyPI is limiting requests",
    )
    page.context.close()

    page = run.open(1280, 900)
    run.load(page, "dependencies.html?package=pypi:fixture-web", ready="#reading .dep-node")
    page.click(node_sel("pypi:fixture-direct"))
    check(
        "releases: a requirement that is a link says there is no registry answer to read dates from",
        "is given as a link, path or alias rather than a package name" in page.locator(".release-host").inner_text()
        and count(page, "#release-load") == 0,
    )
    check("releases: the console is clean after failures", only_http_noise(run.console(page)), run.console(page))
    page.context.close()


def dependencies_release_small(run: Run) -> None:
    check = run.check
    for width, height, name in ((375, 667, "v50-dependencies-releases-phone"), (320, 568, "")):
        page = run.open(width, height, has_touch=True, is_mobile=True)
        run.load(page, "dependencies.html?package=pypi:fixture-web", ready="#reading .dep-node")
        page.tap("#release-load")
        page.wait_for_selector("#reading .release-svg", timeout=20000)
        check(f"releases: {width}px: no horizontal overflow", not page.evaluate(OVERFLOW_X))
        check(
            f"releases: {width}px: the strip scrolls inside its own box",
            page.locator(".release-scroll").evaluate("n => n.scrollWidth > n.clientWidth"),
        )
        page.tap('.release-mark[data-index="21"]')
        check(
            f"releases: {width}px: tapping a mark selects it",
            selected_release(page) == "21" and release_readout(page).startswith("Version 2.0.0, 6 February 2024"),
            release_readout(page),
        )
        small = page.evaluate(
            "[...document.querySelectorAll('.release-block button, .release-block summary')]"
            ".filter(b => b.getBoundingClientRect().height < 24).length"
        )
        check(f"releases: {width}px: buttons and the table toggle are at least 24px tall", small == 0, small)
        page.tap(".release-list summary")
        check(
            f"releases: {width}px: the table scrolls inside its own box, not the page",
            not page.evaluate(OVERFLOW_X)
            and page.locator(".release-table-wrap").evaluate("n => n.scrollHeight >= n.clientHeight"),
        )
        if name:
            page.locator(".release-scroll").evaluate("n => n.scrollIntoView({block: 'start'})")
            run.shot(page, name)
        check(f"releases: {width}px: the console is clean", not run.console(page), run.console(page))
        page.context.close()


def dependencies_switched_off(run: Run) -> None:
    """The server started with --no-registries: the page must say so rather than look broken."""
    check = run.check
    page = run.open(1280, 900)
    run.load(page, "dependencies.html", ready="#reading .idle")
    read_package(page, "pypi", "flask")
    title = page.locator(".failure-title").inner_text()
    check(
        "dependencies: with registries switched off the page says so",
        title == "Package registries are switched off",
        title,
    )
    check("dependencies: it says how to switch them on", "--no-registries" in page.locator(".failure").inner_text())
    check("dependencies: that is not offered as something to retry", count(page, ".failure button") == 0)
    check(
        "dependencies: switched off leaves only the browser's own status messages", only_http_noise(run.console(page))
    )
    run.shot(page, "v47-dependencies-switched-off")
    page.context.close()

def start(extra: list[str]) -> subprocess.Popen[str]:
    """The prototype server, on a free port, with VYRON's own rate limiter off."""
    command = [sys.executable, str(ROOT / "apps" / "web" / "prototype" / "serve.py"), "--port", "0", "--no-rate-limit"]
    return subprocess.Popen(
        command + extra, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )  # fmt: skip


def start_standin() -> subprocess.Popen[str]:
    """The prototype wired to scripts/standin_github.py: FIXTURE DATA, never GitHub."""
    command = [sys.executable, str(ROOT / "scripts" / "standin_github.py"), "--mode", "prototype", "--port", "0",
               "--no-rate-limit"]  # fmt: skip
    return subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )  # fmt: skip


def start_registry_standin() -> subprocess.Popen[str]:
    """The prototype wired to scripts/standin_registry.py: FIXTURE DATA, never PyPI or npm."""
    command = [sys.executable, str(ROOT / "scripts" / "standin_registry.py"), "--mode", "prototype", "--port", "0",
               "--no-rate-limit"]  # fmt: skip
    return subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )  # fmt: skip


def wait_for(process: subprocess.Popen[str], pattern: str) -> str:
    """The first capture group of the first line the process prints that matches, or '' if it exits first."""
    assert process.stdout is not None
    for line in process.stdout:
        match = re.search(pattern, line)
        if match:
            return match.group(1)
    return ""


def stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    if process.stdout:
        process.stdout.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "docs" / "prototype"), help="where to write screenshots")
    parser.add_argument("--chromium", default=os.environ.get("CHROMIUM_PATH"), help="Chromium executable")
    args = parser.parse_args(argv)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("The playwright Python package is required (UNVERIFIED tooling, not a dependency).", file=sys.stderr)
        return 2
    shots = Path(args.out)
    shots.mkdir(parents=True, exist_ok=True)
    # The map checks never need GitHub, PyPI or npm, so this server cannot reach any of them.
    server = start(["--no-github", "--no-registries"])
    standin_server = start_standin()
    try:
        base = wait_for(server, r"Listening on (http://127\.0\.0\.1:\d+/)")
        standin_url = wait_for(standin_server, r"GitHub stand-in \(FIXTURE DATA, NOT FROM GITHUB\): (http://\S+)")
        standin_base = wait_for(standin_server, r"Listening on (http://127\.0\.0\.1:\d+/)")
        if not (base and standin_url and standin_base):
            print("A server did not start. Run: python3 scripts/build_prototype.py", file=sys.stderr)
            return 2
        with sync_playwright() as playwright:
            options: dict[str, Any] = {"args": CHROMIUM_ARGS}
            if args.chromium:
                options["executable_path"] = args.chromium
            browser = playwright.chromium.launch(**options)
            run = Run(base, shots, browser)
            for scenario in (
                desktop, timeline, medium, mobile, other_conditions, repository_switched_off, dependencies_switched_off
            ):
                scenario(run)
            run.base = standin_base
            repository_page(run, standin_url)
            # The activity checks get a stand-in and a server of their own: the scenarios above leave the first
            # server's quota budget spent and its cache holding example-repo, which would change what is asked.
            second = start_standin()
            try:
                second_url = wait_for(second, r"GitHub stand-in \(FIXTURE DATA, NOT FROM GITHUB\): (http://\S+)")
                second_base = wait_for(second, r"Listening on (http://127\.0\.0\.1:\d+/)")
                if not (second_url and second_base):
                    print("The second stand-in did not start.", file=sys.stderr)
                    return 2
                run.base = second_base
                repository_activity(run, second_url)
            finally:
                stop(second)
            third = start_standin()  # its own counts of 202s, which the contributors checks above would disturb
            try:
                third_url = wait_for(third, r"GitHub stand-in \(FIXTURE DATA, NOT FROM GITHUB\): (http://\S+)")
                third_base = wait_for(third, r"Listening on (http://127\.0\.0\.1:\d+/)")
                if not (third_url and third_base):
                    print("The third stand-in did not start.", file=sys.stderr)
                    return 2
                run.base = third_base
                commit_activity(run, third_url)
            finally:
                stop(third)
            fourth = start_registry_standin()
            try:
                fourth_url = wait_for(fourth, r"Registry stand-in \(FIXTURE DATA, NOT FROM PYPI OR NPM\): (http://\S+)")
                fourth_base = wait_for(fourth, r"Listening on (http://127\.0\.0\.1:\d+/)")
                if not (fourth_url and fourth_base):
                    print("The registry stand-in did not start.", file=sys.stderr)
                    return 2
                run.base = fourth_base
                dependencies_page(run, fourth_url)
                dependencies_shapes(run, fourth_url)
                dependencies_failures(run)
                dependencies_npm(run, fourth_url)
                dependencies_releases(run, fourth_url)
                dependencies_release_wide(run, fourth_url)
                dependencies_release_failures(run, fourth_url)
                dependencies_small(run)
                dependencies_release_small(run)
            finally:
                stop(fourth)
            browser.close()
    finally:
        for process in (server, standin_server):
            stop(process)
    failed = [r for r in run.results if not r[1]]
    print(f"\n{len(run.results) - len(failed)} passed, {len(failed)} failed")
    for name, _, detail in failed:
        print("FAILED:", name, detail)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
