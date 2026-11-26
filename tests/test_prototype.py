"""The browser prototype's server and page contract, checked without a browser.

Covers what can be verified with the standard library alone: the composite server (static files plus the
real API on one origin, strict Content-Security-Policy, no path traversal), that the page uses no inline
script, inline style or external resource, that every element the script looks up exists in the HTML,
that every design token the stylesheet uses is defined, and that the prototype's source never assembles
markup from strings. What this cannot check, how it looks and behaves in a real browser, is covered by the
Chromium run recorded in docs/PROTOTYPE.md.

Run from the repository root:  python3 -m unittest discover -s tests
"""

import _paths  # noqa: F401

import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any
from wsgiref.util import setup_testing_defaults

from _paths import ROOT

PROTOTYPE = ROOT / "apps" / "web" / "prototype"
TOKENS_CSS = ROOT / "apps" / "web" / "src" / "styles" / "tokens.css"


def load_server() -> Any:
    spec = importlib.util.spec_from_file_location("vyron_prototype_serve", PROTOTYPE / "serve.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


serve = load_server()


class Response:
    def __init__(self, status: str, headers: list[tuple[str, str]], body: bytes) -> None:
        self.status = int(status.split()[0])
        self.headers = {name.lower(): value for name, value in headers}
        self.body = body


def call(app: Any, path: str, method: str = "GET", query: str = "", accept: str = "") -> Response:
    environ: dict[str, Any] = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query}
    if accept:
        environ["HTTP_ACCEPT"] = accept
    setup_testing_defaults(environ)
    environ["REMOTE_ADDR"] = "127.0.0.1"
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
        captured["status"], captured["headers"] = status, headers

    body = b"".join(app(environ, start_response))
    return Response(captured["status"], captured["headers"], body)


class ServerTestCase(unittest.TestCase):
    """A server over a throw-away build directory, so no test depends on a compiled build existing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dist = Path(self._tmp.name) / "js"
        (self.dist / "apps" / "web" / "prototype" / "src").mkdir(parents=True)
        entry = self.dist / "apps" / "web" / "prototype" / "src" / "main.js"
        entry.write_text("export const ok = true;\n", encoding="utf-8")
        (self.dist / "notes.txt").write_text("not a script", encoding="utf-8")
        (self.dist / "main.js.map").write_text("{}", encoding="utf-8")
        self.outside = Path(self._tmp.name) / "outside.js"
        self.outside.write_text("secret();", encoding="utf-8")
        self.app = serve.make_app(dist=self.dist, rate_limit=False)


class StaticServing(ServerTestCase):
    def test_page_is_served_with_a_strict_content_security_policy(self) -> None:
        response = call(self.app, "/")
        self.assertEqual(response.status, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        policy = response.headers["content-security-policy"]
        for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'", "object-src 'none'",
                          "base-uri 'none'", "frame-ancestors 'none'", "connect-src 'self'"):  # fmt: skip
            self.assertIn(directive, policy)
        self.assertNotIn("unsafe", policy)
        self.assertNotIn("*", policy)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.body, (PROTOTYPE / "index.html").read_bytes())

    def test_assets_have_the_right_types(self) -> None:
        expected = {
            "/prototype.css": "text/css",
            "/tokens.css": "text/css",
            "/favicon.svg": "image/svg+xml",
            "/index.html": "text/html",
            "/js/apps/web/prototype/src/main.js": "text/javascript",
        }
        for path, content_type in expected.items():
            with self.subTest(path=path):
                response = call(self.app, path)
                self.assertEqual(response.status, 200)
                self.assertTrue(response.headers["content-type"].startswith(content_type), response.headers)
                self.assertIn("content-security-policy", response.headers)
                self.assertEqual(int(response.headers["content-length"]), len(response.body))

    def test_the_repository_page_has_the_same_strict_policy_and_the_right_body(self) -> None:
        response = call(self.app, "/repository.html")
        self.assertEqual(response.status, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        policy = response.headers["content-security-policy"]
        self.assertEqual(policy, call(self.app, "/").headers["content-security-policy"])
        self.assertNotIn("unsafe", policy)
        self.assertIn("connect-src 'self'", policy)  # the page reaches GitHub only through this server
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.body, (PROTOTYPE / "repository.html").read_bytes())
        self.assertEqual(call(self.app, "/repository.html", method="HEAD").body, b"")

    def test_the_dependencies_page_has_the_same_strict_policy_and_the_right_body(self) -> None:
        response = call(self.app, "/dependencies.html")
        self.assertEqual(response.status, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        policy = response.headers["content-security-policy"]
        self.assertEqual(policy, call(self.app, "/").headers["content-security-policy"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.body, (PROTOTYPE / "dependencies.html").read_bytes())
        self.assertEqual(call(self.app, "/dependencies.html", method="HEAD").body, b"")

    def test_the_compare_page_has_the_same_strict_policy_and_the_right_body(self) -> None:
        response = call(self.app, "/compare.html")
        self.assertEqual(response.status, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        policy = response.headers["content-security-policy"]
        self.assertEqual(policy, call(self.app, "/").headers["content-security-policy"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.body, (PROTOTYPE / "compare.html").read_bytes())
        self.assertEqual(call(self.app, "/compare.html", method="HEAD").body, b"")

    def test_seo_aeo_and_geo_discovery_assets_are_served_with_valid_content(self) -> None:
        expected = {
            "/robots.txt": "text/plain",
            "/sitemap.xml": "application/xml",
            "/llms.txt": "text/plain",
            "/llms-full.txt": "text/plain",
            "/.well-known/llms.txt": "text/plain",
            "/schema.jsonld": "application/ld+json",
            "/site.webmanifest": "application/manifest+json",
            "/favicon.ico": "image/x-icon",
            "/og-image.png": "image/png",
            "/about": "text/html",
            "/contact": "text/html",
            "/privacy": "text/html",
        }
        for path, content_type in expected.items():
            with self.subTest(path=path):
                response = call(self.app, path)
                self.assertEqual(response.status, 200)
                self.assertTrue(response.headers["content-type"].startswith(content_type), response.headers)
                self.assertIn("content-security-policy", response.headers)
        schema = json.loads(call(self.app, "/schema.jsonld").body)
        self.assertEqual(schema["@context"], "https://schema.org")
        self.assertGreaterEqual(len(schema["@graph"]), 5)
        types = {node.get("@type") for node in schema["@graph"]}
        self.assertTrue({"Organization", "WebSite", "SoftwareApplication", "FAQPage"} <= types)
        manifest = json.loads(call(self.app, "/site.webmanifest").body)
        self.assertEqual(manifest["short_name"], "VYRON")
        robots = call(self.app, "/robots.txt").body.decode("utf-8")
        self.assertIn("Sitemap: https://vyron-orpin.vercel.app/sitemap.xml", robots)
        sitemap = call(self.app, "/sitemap.xml").body.decode("utf-8")
        self.assertIn("<loc>https://vyron-orpin.vercel.app/</loc>", sitemap)
        self.assertIn("<loc>https://vyron-orpin.vercel.app/about</loc>", sitemap)
        llms = call(self.app, "/llms.txt").body.decode("utf-8")
        self.assertIn("not production ready", llms)
        self.assertIn("## When to Use VYRON", llms)

    def test_markdown_content_negotiation_and_agent_friendly_404(self) -> None:
        html = call(self.app, "/", accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
        self.assertEqual(html.status, 200)
        self.assertTrue(html.headers["content-type"].startswith("text/html"))
        self.assertEqual(html.headers.get("vary"), "Accept")
        for path in ("/", "/repository.html", "/dependencies.html", "/compare.html", "/about", "/contact", "/privacy"):
            with self.subTest(path=path):
                md = call(self.app, path, accept="text/markdown")
                self.assertEqual(md.status, 200)
                self.assertTrue(md.headers["content-type"].startswith("text/markdown"))
                self.assertEqual(md.headers.get("vary"), "Accept")
                self.assertIn("# ", md.body.decode("utf-8"))
        missing_md = call(self.app, "/no-such-page", accept="text/markdown")
        self.assertEqual(missing_md.status, 404)
        self.assertTrue(missing_md.headers["content-type"].startswith("text/markdown"))
        self.assertEqual(missing_md.headers.get("vary"), "Accept")
        self.assertIn("/llms.txt", missing_md.body.decode("utf-8"))

    def test_vercel_wsgi_entrypoint_serves_pages_discovery_files_and_api(self) -> None:
        spec = importlib.util.spec_from_file_location("vyron_vercel_index", ROOT / "api" / "index.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        vercel_app = module.app
        for path in ("/", "/repository.html", "/dependencies.html", "/compare.html", "/about", "/contact",
                     "/privacy", "/robots.txt", "/llms.txt", "/llms-full.txt", "/schema.jsonld", "/sitemap.xml",
                     "/site.webmanifest", "/favicon.ico", "/og-image.png", "/api/v1/meta", "/health/live"):
            with self.subTest(path=path):
                self.assertEqual(call(vercel_app, path).status, 200)

    def test_vercelignore_anchors_top_level_directories_so_runtime_packages_stay_bundled(self) -> None:
        lines = [
            line.strip()
            for line in (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertNotIn("services", lines, "unanchored 'services' strips apps/api/src/vyron_api/services on Vercel")
        self.assertIn("/services", lines)
        for name in ("docs", "infrastructure", "tests"):
            self.assertNotIn(name, lines)
            self.assertIn(f"/{name}", lines)

    def test_the_served_tokens_are_the_generated_design_tokens(self) -> None:
        self.assertEqual(call(self.app, "/tokens.css").body, TOKENS_CSS.read_bytes())

    def test_head_matches_get_without_a_body(self) -> None:
        got, head = call(self.app, "/"), call(self.app, "/", method="HEAD")
        self.assertEqual(head.status, 200)
        self.assertEqual(head.body, b"")
        self.assertEqual(head.headers["content-length"], got.headers["content-length"])

    def test_only_get_and_head_are_allowed_on_static_paths(self) -> None:
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                response = call(self.app, "/", method=method)
                self.assertEqual(response.status, 405)
                self.assertEqual(response.headers["allow"], "GET, HEAD")


class PathSafety(ServerTestCase):
    def test_traversal_is_refused(self) -> None:
        attempts = [
            "/js/../../../../etc/passwd",
            "/js/../index.html",
            "/js/../../prototype.css",
            "/js/apps/../../../outside.js",
            "/js//etc/passwd",
            "/js/..\\..\\outside.js",
            "/js/apps/web/prototype/src/main.js\x00.txt",
            "/js/%2e%2e/%2e%2e/outside.js",
        ]
        for path in attempts:
            with self.subTest(path=path):
                self.assertEqual(call(self.app, path).status, 404)

    def test_only_javascript_files_are_served_from_the_build(self) -> None:
        self.assertEqual(call(self.app, "/js/notes.txt").status, 404)
        self.assertEqual(call(self.app, "/js/main.js.map").status, 404)
        self.assertEqual(call(self.app, "/js/apps/web/prototype/src").status, 404)

    def test_a_symlink_out_of_the_build_is_not_followed(self) -> None:
        link = self.dist / "escape.js"
        try:
            link.symlink_to(self.outside)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available here")
        self.assertEqual(call(self.app, "/js/escape.js").status, 404)

    def test_source_files_are_not_reachable(self) -> None:
        for path in (
            "/serve.py", "/prototype/serve.py", "/src/main.ts", "/tsconfig.json", "/dist/js/x.js",
            "/index.html.bak", "/apps/api/src/vyron_api/config.py", "/.env", "/tokens.json",
        ):  # fmt: skip
            with self.subTest(path=path):
                response = call(self.app, path)
                self.assertEqual(response.status, 404)
                self.assertTrue(response.headers["content-type"].startswith("text/plain"))

    def test_a_missing_build_says_how_to_build(self) -> None:
        empty = serve.make_app(dist=self.dist / "nowhere", rate_limit=False)
        response = call(empty, "/js/apps/web/prototype/src/main.js")
        self.assertEqual(response.status, 404)

    def test_the_command_refuses_to_start_without_a_build(self) -> None:
        original = serve.DIST_JS
        serve.DIST_JS = self.dist / "nowhere"
        try:
            errors = io.StringIO()
            with redirect_stderr(errors):
                code = serve.main(["--port", "0"])
        finally:
            serve.DIST_JS = original
        self.assertEqual(code, 2)
        self.assertIn("build_prototype.py", errors.getvalue())


class ApiOnTheSameOrigin(ServerTestCase):
    def test_the_real_api_answers_under_api_and_health(self) -> None:
        meta = call(self.app, "/api/v1/meta")
        self.assertEqual(meta.status, 200)
        body = json.loads(meta.body)
        self.assertEqual(body["data_mode"], "seed")
        self.assertIn("seed data", body["data_notice"].lower())
        self.assertEqual(call(self.app, "/health/live").status, 200)

    def test_api_responses_keep_the_apis_own_stricter_policy(self) -> None:
        policy = call(self.app, "/api/v1/meta").headers["content-security-policy"]
        self.assertEqual(policy, "default-src 'none'; frame-ancestors 'none'")

    def test_the_overview_has_the_cluster_members_the_map_draws_from(self) -> None:
        body = json.loads(call(self.app, "/api/v1/graph/overview", query="clusters=20&top=20").body)
        self.assertEqual((body["entity_count"], body["relationship_count"]), (60, 68))
        self.assertEqual(len(body["clusters"]), 20)
        members = [m for cluster in body["clusters"] for m in cluster["member_ids"]]
        self.assertEqual(len(members), 60)
        self.assertEqual(len(set(members)), 60)

    def test_write_methods_reach_the_api_not_the_static_405(self) -> None:
        response = call(self.app, "/api/v1/entities", method="POST")
        self.assertNotEqual(response.headers.get("allow"), "GET, HEAD")
        self.assertIn(response.status, (404, 405))

    def test_the_rate_limiter_is_on_by_default_and_can_be_turned_off(self) -> None:
        def statuses(app: Any) -> set[int]:
            return {call(app, "/api/v1/graph/overview", query="clusters=1&top=1").status for _ in range(70)}

        self.assertIn(429, statuses(serve.make_app(dist=self.dist)))
        self.assertEqual(statuses(serve.make_app(dist=self.dist, rate_limit=False)), {200})


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.inline_scripts = 0
        self.json_ld_blocks: list[str] = []
        self.style_elements = 0
        self.style_attributes = 0
        self.event_attributes: list[str] = []
        self.references: list[tuple[str, str]] = []
        self._in_script = False
        self._script_has_src = False
        self._script_is_json_ld = False
        self._json_ld_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if "id" in attributes:
            self.ids.add(attributes["id"])
        self.classes.update(attributes.get("class", "").split())
        if "style" in attributes:
            self.style_attributes += 1
        self.event_attributes += [name for name in attributes if name.startswith("on")]
        if tag == "style":
            self.style_elements += 1
        if tag == "script":
            self._in_script = True
            self._script_has_src = "src" in attributes
            self._script_is_json_ld = attributes.get("type", "").strip().lower() == "application/ld+json"
            self._json_ld_chunks = []
        for name in ("src", "href"):
            if name in attributes and tag in ("script", "link", "img"):
                if tag == "link" and attributes.get("rel", "").strip().lower() == "canonical":
                    continue
                self.references.append((tag, attributes[name]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            if self._script_is_json_ld:
                self.json_ld_blocks.append("".join(self._json_ld_chunks))
            self._in_script = False
            self._script_is_json_ld = False
            self._json_ld_chunks = []

    def handle_data(self, data: str) -> None:
        if self._in_script and self._script_is_json_ld:
            self._json_ld_chunks.append(data)
        elif self._in_script and not self._script_has_src and data.strip():
            self.inline_scripts += 1


def strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$|(?<=\s)//\s.*$", "", source)


if TYPE_CHECKING:
    _MixinBase = unittest.TestCase
else:
    _MixinBase = object


class PageContractTests(_MixinBase):
    """What every page of the prototype promises; each page binds its own HTML file and script.

    A mixin rather than a TestCase, so the runner never collects the unbound version.
    """

    HTML_FILE = "index.html"
    SCRIPT_FILE = "main.ts"
    MIN_LOOKUPS = 15
    SKIP_TARGET = "stage"
    LANDMARKS: tuple[str, ...] = ("<header", "<main", "<aside", "<footer")

    html: str
    page: PageParser

    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (PROTOTYPE / cls.HTML_FILE).read_text(encoding="utf-8")
        cls.page = PageParser()
        cls.page.feed(cls.html)

    def test_no_inline_script_style_or_handler(self) -> None:
        self.assertEqual(self.page.inline_scripts, 0)
        self.assertEqual(self.page.style_elements, 0)
        self.assertEqual(self.page.style_attributes, 0)
        self.assertEqual(self.page.event_attributes, [])

    def test_nothing_is_loaded_from_another_origin(self) -> None:
        self.assertTrue(self.page.references)
        for _tag, target in self.page.references:
            self.assertTrue(target.startswith("/") and not target.startswith("//"), target)
        self.assertTrue(self.page.json_ld_blocks, "every page should embed valid JSON-LD structured data")
        for block in self.page.json_ld_blocks:
            parsed = json.loads(block)
            self.assertEqual(parsed.get("@context"), "https://schema.org")

    def test_every_referenced_asset_is_served(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            for _tag, target in self.page.references:
                if target.startswith("/js/"):
                    file = dist / target[len("/js/") :]
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_text("export {};\n", encoding="utf-8")
            app = serve.make_app(dist=dist, rate_limit=False)
            for tag, target in self.page.references:
                with self.subTest(target=target):
                    self.assertEqual(call(app, target).status, 200, f"<{tag}> points at {target}")

    def test_the_page_declares_its_language_viewport_and_title(self) -> None:
        self.assertIn('<html lang="en">', self.html)
        self.assertIn("width=device-width", self.html)
        self.assertRegex(self.html, r"<title>[^<]+</title>")
        for marker in ('name="robots"', 'rel="canonical"', 'property="og:title"', 'property="og:image"',
                       'name="twitter:card"', 'href="/schema.jsonld"', 'href="/llms.txt"', 'href="/site.webmanifest"'):
            self.assertIn(marker, self.html)

    def test_the_page_script_is_the_one_the_html_loads(self) -> None:
        module = "/js/apps/web/prototype/src/" + self.SCRIPT_FILE.removesuffix(".ts") + ".js"
        self.assertIn(("script", module), self.page.references)

    def test_every_element_the_script_looks_up_exists(self) -> None:
        source = (PROTOTYPE / "src" / self.SCRIPT_FILE).read_text(encoding="utf-8")
        wanted = set(re.findall(r'\bel(?:<[^>]+>)?\("([A-Za-z0-9_-]+)"\)', source))
        wanted |= set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', source))
        self.assertGreater(len(wanted), self.MIN_LOOKUPS, wanted)
        self.assertEqual(
            sorted(wanted - self.page.ids), [], f"{self.SCRIPT_FILE} looks up ids that {self.HTML_FILE} does not have"
        )

    def test_the_skip_link_and_landmarks_are_present(self) -> None:
        self.assertRegex(self.html, rf'<a class="skip" href="#{self.SKIP_TARGET}">')
        for landmark in self.LANDMARKS:
            self.assertIn(landmark, self.html)
        self.assertIn(f'id="{self.SKIP_TARGET}"', self.html)

    def test_the_live_region_exists(self) -> None:
        self.assertRegex(self.html, r'id="live"[^>]*aria-live="polite"')

    def test_every_page_links_to_the_others(self) -> None:
        pages = {
            "/": "index.html",
            "/repository.html": "repository.html",
            "/dependencies.html": "dependencies.html",
            "/compare.html": "compare.html",
        }
        links = re.findall(r'<a class="page-link[^"]*" href="([^"]+)"', self.html)
        self.assertEqual(sorted(set(links)), sorted(path for path, file in pages.items() if file != self.HTML_FILE))


class PageContract(PageContractTests, unittest.TestCase):
    """The map page."""

    def test_the_search_status_region_exists(self) -> None:
        self.assertRegex(self.html, r'id="search-status"[^>]*aria-live="polite"')


class RepositoryPageContract(PageContractTests, unittest.TestCase):
    """The repository page: the one place the prototype shows data that is not seed data."""

    HTML_FILE = "repository.html"
    SCRIPT_FILE = "repository.ts"
    MIN_LOOKUPS = 5
    SKIP_TARGET = "reading"
    LANDMARKS = ("<header", "<main", "<footer")

    def test_it_has_a_labelled_form_with_a_submit_button(self) -> None:
        self.assertIn("repo-form", self.page.ids)
        self.assertRegex(self.html, r'<label class="sr-only" for="repo-input">')
        self.assertRegex(self.html, r'<button type="submit"[^>]*id="repo-submit"')

    def test_it_does_not_borrow_a_class_the_map_gives_its_own_meaning(self) -> None:
        # `.reading` is the map dossier's serif prose style; putting it on the page's container once restyled
        # every figure inside it. The container has its own class.
        self.assertNotIn("reading", self.page.classes)
        self.assertIn("repo-reading", self.page.classes)

    def test_it_never_claims_the_figures_are_seed_data_or_stored(self) -> None:
        self.assertIn("Nothing is stored", self.html)
        self.assertNotIn("seed", self.html.lower())

    def test_every_label_target_the_script_builds_is_one_it_also_defines(self) -> None:
        source = (PROTOTYPE / "src" / "repository.ts").read_text(encoding="utf-8")
        referenced = set(re.findall(r'"aria-labelledby":\s*"([a-z0-9-]+)"', source))
        defined = set(re.findall(r'\bid:\s*"([a-z0-9-]+)"', source))
        self.assertTrue(referenced)
        self.assertEqual(sorted(referenced - defined - self.page.ids), [])

    def test_the_script_never_names_a_host_so_it_cannot_reach_github_itself(self) -> None:
        for name in ("repository.ts", "repository-model.ts"):
            source = strip_comments((PROTOTYPE / "src" / name).read_text(encoding="utf-8"))
            with self.subTest(file=name):
                self.assertNotRegex(source, r"api\.github\.com|https?://")


class DependenciesPageContract(PageContractTests, unittest.TestCase):
    """The dependencies page: PyPI and npm packages read one at a time, never seed data."""

    HTML_FILE = "dependencies.html"
    SCRIPT_FILE = "dependencies.ts"
    MIN_LOOKUPS = 6
    SKIP_TARGET = "reading"
    LANDMARKS = ("<header", "<main", "<footer")

    def test_it_has_a_labelled_form_with_a_registry_choice_and_a_submit_button(self) -> None:
        self.assertIn("dep-form", self.page.ids)
        self.assertRegex(self.html, r'<label class="sr-only" for="dep-input">')
        self.assertRegex(self.html, r'<label class="sr-only" for="dep-registry">')
        self.assertRegex(self.html, r'<button type="submit"[^>]*id="dep-submit"')
        self.assertIn('value="pypi"', self.html)
        self.assertIn('value="npm"', self.html)

    def test_it_says_what_it_keeps_and_never_calls_the_answers_seed_data(self) -> None:
        self.assertIn("nothing is stored in your browser", self.html)
        self.assertNotIn("seed", self.html.lower())

    def test_it_does_not_borrow_a_class_the_map_gives_its_own_meaning(self) -> None:
        self.assertNotIn("reading", self.page.classes)

    def test_every_label_target_the_script_builds_is_one_it_also_defines(self) -> None:
        source = (PROTOTYPE / "src" / "dependencies.ts").read_text(encoding="utf-8")
        referenced = set(re.findall(r'"aria-labelledby":\s*"([a-z0-9-]+)"', source))
        defined = set(re.findall(r'\bid:\s*"([a-z0-9-]+)"', source))
        self.assertTrue(referenced)
        self.assertEqual(sorted(referenced - defined - self.page.ids), [])

    def test_the_scripts_never_name_a_registry_host_so_they_cannot_reach_one_themselves(self) -> None:
        for name in ("dependencies.ts", "dependencies-model.ts", "release-view.ts", "releases-model.ts"):
            source = strip_comments((PROTOTYPE / "src" / name).read_text(encoding="utf-8"))
            with self.subTest(file=name):
                self.assertNotRegex(source, r"pypi\.org/pypi|registry\.npmjs\.org|api\.github\.com|fetch\(")
        # Only the model reads pasted links, so only it may spell a scheme; the page script has no address in it.
        page = strip_comments((PROTOTYPE / "src" / "dependencies.ts").read_text(encoding="utf-8"))
        self.assertNotRegex(page, r"https?://")


class ComparePageContract(PageContractTests, unittest.TestCase):
    """The side-by-side compare page: live GitHub repositories or PyPI/npm packages, never seed data."""

    HTML_FILE = "compare.html"
    SCRIPT_FILE = "compare.ts"
    MIN_LOOKUPS = 6
    SKIP_TARGET = "reading"
    LANDMARKS = ("<header", "<main", "<footer")

    def test_it_has_a_labelled_form_with_mode_and_two_inputs(self) -> None:
        self.assertIn("compare-form", self.page.ids)
        self.assertRegex(self.html, r'<label class="sr-only" for="compare-mode">')
        self.assertRegex(self.html, r'<label class="sr-only" for="compare-a">')
        self.assertRegex(self.html, r'<label class="sr-only" for="compare-b">')
        self.assertRegex(self.html, r'<button type="submit"[^>]*id="compare-submit"')

    def test_it_says_nothing_is_stored_and_never_claims_seed_data(self) -> None:
        self.assertIn("nothing is stored in your browser", self.html)
        self.assertNotIn("seed", self.html.lower())

    def test_it_does_not_borrow_a_class_the_map_gives_its_own_meaning(self) -> None:
        self.assertNotIn("reading", self.page.classes)

    def test_every_label_target_the_script_builds_is_one_it_also_defines(self) -> None:
        source = (PROTOTYPE / "src" / "compare.ts").read_text(encoding="utf-8")
        referenced = set(re.findall(r'"aria-labelledby":\s*"([a-z0-9-]+)"', source))
        defined = set(re.findall(r'\bid:\s*"([a-z0-9-]+)"', source))
        self.assertTrue(referenced)
        self.assertEqual(sorted(referenced - defined - self.page.ids), [])

    def test_the_compare_scripts_never_name_a_host_or_call_fetch(self) -> None:
        for name in ("compare.ts", "compare-model.ts"):
            source = strip_comments((PROTOTYPE / "src" / name).read_text(encoding="utf-8"))
            with self.subTest(file=name):
                self.assertNotRegex(source, r"pypi\.org/pypi|registry\.npmjs\.org|api\.github\.com|https?://|fetch\(")


class StylesheetContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.css = (PROTOTYPE / "prototype.css").read_text(encoding="utf-8")
        cls.tokens = TOKENS_CSS.read_text(encoding="utf-8")

    def test_every_token_it_uses_is_defined(self) -> None:
        defined = set(re.findall(r"(--vy-[a-z0-9-]+)\s*:", self.tokens))
        used = set(re.findall(r"var\((--vy-[a-z0-9-]+)", self.css))
        self.assertGreater(len(used), 30)
        self.assertEqual(sorted(used - defined), [], "prototype.css uses design tokens that tokens.css does not define")

    def test_it_defines_no_colour_of_its_own(self) -> None:
        # Every colour comes from the generated tokens; a hex or rgb() literal here would fork the palette.
        without_comments = re.sub(r"/\*.*?\*/", "", self.css, flags=re.DOTALL)
        without_references = re.sub(r"url\(#[^)]*\)", "", without_comments)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", without_references), [])

    def test_it_loads_nothing_and_forces_nothing(self) -> None:
        # A fragment reference (url(#marker)) points inside the page; anything else would be a request.
        self.assertIsNone(re.search(r"@import|url\((?!#)", self.css), "the stylesheet must not load anything")
        self.assertNotIn("!important", self.css)

    def test_it_honours_reduced_motion_and_keyboard_focus(self) -> None:
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertIn(":focus-visible", self.css)
        self.assertIn("forced-colors", self.css)

    def test_it_styles_every_class_the_map_builds(self) -> None:
        classes = set()
        names = (
            "map.ts",
            "panels.ts",
            "main.ts",
            "marks.ts",
            "repository.ts",
            "time-strip.ts",
            "dependencies.ts",
            "release-view.ts",
            "compare.ts",
        )
        for name in names:
            text = (PROTOTYPE / "src" / name).read_text(encoding="utf-8")
            classes |= set(re.findall(r'class: "([a-z][a-z0-9 -]*)"', text))
        known = {c for group in classes for c in group.split()}
        selectors = set(re.findall(r"\.([a-z][a-z0-9-]*)", self.css))
        # Wrappers that exist for structure (SVG layers, containers) and need no rule of their own.
        script_only = {
            "hit", "nodes", "edges", "labels", "edge-labels", "cluster-links", "territories",
            "kind-group", "kind-groups", "timeline-host", "notice-mark", "time-body",
            "report", "report-head",  # structural wrappers on the repository page; their children carry the styles
            "dep-nodes", "dep-edges", "release-marks",  # SVG layers on the dependencies page
        }  # fmt: skip
        missing = sorted(c for c in known - selectors - script_only)
        self.assertEqual(missing, [], "classes created by the scripts that prototype.css never styles")

    def test_the_repository_section_never_redefines_a_class_the_map_already_styles(self) -> None:
        # Both pages share this stylesheet, so a plain `.name { … }` rule in the repository section lands on
        # the map's elements of that name too. Two real collisions got through before this check: `.bar` (the
        # top bar became a block, so its search and links spilled over the page) and `.reading` (dossier prose).
        marker = "/* ---- repository page"
        self.assertEqual(self.css.count(marker), 1)
        before, after = self.css.split(marker)
        own = re.compile(r"^\.([a-z][a-z0-9-]*)(?=[\s,{])", re.MULTILINE)
        shared = set(own.findall(before)) & set(own.findall(after))
        self.assertEqual(sorted(shared), [], "repository rules that restyle a class the map page already defines")

    def test_it_styles_every_class_the_pages_declare(self) -> None:
        selectors = set(re.findall(r"\.([a-z][a-z0-9-]*)", self.css))
        for name in (
            "index.html",
            "repository.html",
            "dependencies.html",
            "compare.html",
            "about.html",
            "contact.html",
            "privacy.html",
        ):
            page = PageParser()
            page.feed((PROTOTYPE / name).read_text(encoding="utf-8"))
            # The screen-reader class and the skip link are styled; nothing else may be left bare.
            with self.subTest(page=name):
                self.assertEqual(sorted(page.classes - selectors), [], f"{name} uses classes with no rule")


def css_blocks(text: str) -> list[tuple[str, str]]:
    """The top-level `prelude { body }` pairs of a stylesheet, comments removed. Enough CSS for this stylesheet."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    found, depth, start, head = [], 0, 0, 0
    prelude = ""
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                prelude, start = text[head:index].strip(), index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                found.append((prelude, text[start:index]))
                head = index + 1
    return found


def overridden_in_media(css: str) -> list[str]:
    """Media-query rules that a plain rule for the same selector, later in the file, silences.

    Same selector and same specificity means the later rule wins, wherever the media query is. Two phone rules and
    three forced-colors rules were lost this way, each written above the base rule it was meant to override.
    """

    def declared(body: str) -> set[str]:
        return {m.group(1) for m in re.finditer(r"(?:^|;)\s*([a-z-]+)\s*:", body)}

    def selectors(prelude: str) -> list[str]:
        return [" ".join(part.split()) for part in prelude.split(",")]

    plain: list[tuple[int, str, set[str]]] = []
    inside: list[tuple[int, str, str, set[str]]] = []
    for order, (prelude, body) in enumerate(css_blocks(css)):
        if prelude.startswith("@media"):
            for inner, inner_body in css_blocks(body):
                inside.extend((order, prelude, sel, declared(inner_body)) for sel in selectors(inner))
        elif not prelude.startswith("@"):
            plain.extend((order, sel, declared(body)) for sel in selectors(prelude))
    return sorted(
        {
            f"{sel} in {query} is overridden for {', '.join(sorted(props & later))}"
            for order, query, sel, props in inside
            for later_order, later_sel, later in plain
            if later_order > order and later_sel == sel and props & later
        }
    )


class CascadeOrder(unittest.TestCase):
    def test_no_media_rule_is_silenced_by_a_later_plain_rule(self) -> None:
        css = (PROTOTYPE / "prototype.css").read_text(encoding="utf-8")
        self.assertGreater(len(css_blocks(css)), 100)
        self.assertEqual(overridden_in_media(css), [])

    def test_the_check_finds_the_mistake_it_guards_against(self) -> None:
        wrong = "@media (max-width: 600px) { .a { display: block; color: red } }\n.a { display: none }"
        right = ".a { display: none }\n@media (max-width: 600px) { .a { display: block } }"
        apart = "@media (max-width: 600px) { .a { color: red } }\n.a { display: none }"
        self.assertEqual(overridden_in_media(wrong), [".a in @media (max-width: 600px) is overridden for display"])
        self.assertEqual(overridden_in_media(right), [])
        self.assertEqual(overridden_in_media(apart), [])  # a different property is no conflict


class SourceHygiene(unittest.TestCase):
    FORBIDDEN = {
        "innerHTML": r"\.innerHTML\b",
        "outerHTML": r"\.outerHTML\b",
        "insertAdjacentHTML": r"insertAdjacentHTML",
        "document.write": r"document\.write",
        "eval": r"\beval\s*\(",
        "new Function": r"new\s+Function\s*\(",
        "inline style property": r"\.style\.[a-zA-Z]+\s*=",
        "inline style attribute": r"""setAttribute\(\s*["']style["']""",
        "string timers": r"""set(?:Timeout|Interval)\(\s*["'`]""",
        "localStorage": r"\blocalStorage\b|\bsessionStorage\b",
    }

    def test_the_prototype_never_builds_markup_from_strings_or_styles_inline(self) -> None:
        files = sorted((PROTOTYPE / "src").glob("*.ts"))
        self.assertGreaterEqual(len(files), 10)
        for file in files:
            source = strip_comments(file.read_text(encoding="utf-8"))
            for name, pattern in self.FORBIDDEN.items():
                with self.subTest(file=file.name, rule=name):
                    self.assertIsNone(re.search(pattern, source), f"{file.name} uses {name}")

    def test_the_prototype_makes_no_requests_except_through_the_client(self) -> None:
        for file in sorted((PROTOTYPE / "src").glob("*.ts")):
            if file.name.endswith(".test.ts"):
                continue
            source = strip_comments(file.read_text(encoding="utf-8"))
            with self.subTest(file=file.name):
                self.assertIsNone(re.search(r"\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource", source))


if __name__ == "__main__":
    unittest.main()
