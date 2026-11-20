import asyncio
import json
import logging
import unittest
from typing import Any
from urllib.parse import urlencode

from github_fakes import FIXTURE_REF, FakeGitHubProvider
from registry_provider_fakes import FakePackageProvider
from vyron_api.adapters.memory_graph import InMemoryGraph
from vyron_api.adapters.memory_timeline import InMemoryTimeline
from vyron_api.api import limits
from vyron_api.api.app import SECURITY_HEADERS, Application, Request, Response
from vyron_api.api.cursor import CursorCodec
from vyron_api.api.handlers import HANDLERS
from vyron_api.api.hostcheck import LOOPBACK_HOSTS
from vyron_api.api.ratelimit import DEFAULT_LIMITS, FixedWindowRateLimiter, Limit
from vyron_api.api.routes import OPERATIONS, ROUTES, RateBucket, Status
from vyron_api.api.schemas import COMPONENTS
from vyron_api.api.validate import validate
from vyron_api.domain.entities import Entity, EntityKind, Relationship, RelationType
from vyron_api.domain.provenance import SEED_NOTICE, Provenance
from vyron_api.github.normalize import MalformedGitHubPayloadError
from vyron_api.providers.github import (
    GitHubAuthError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)
from vyron_api.providers.graph import GraphUnavailableError
from vyron_api.seed.dev_seed import build_seed_graph, entity_id, seed_timeline
from vyron_api.services.catalog import CatalogService

K = EntityKind
PYTHON = entity_id(K.LANGUAGE, "python")
JS = entity_id(K.LANGUAGE, "javascript")
REACT = entity_id(K.LIBRARY, "react")
NEXT = entity_id(K.FRAMEWORK, "next-js")
NVIDIA = entity_id(K.COMPANY, "nvidia")
SECRET = b"t" * 32


_FAKE_GITHUB = object()


def make_app(
    graph: InMemoryGraph | None = None, github: Any = _FAKE_GITHUB, packages: Any = _FAKE_GITHUB, **kwargs: Any
) -> Application:
    """The seed app. ``github`` and ``packages`` default to fixture providers; pass ``None`` for "switched off"."""
    graph = graph or build_seed_graph()
    service = CatalogService(
        graph, graph, InMemoryTimeline(seed_timeline()), CursorCodec([SECRET]),
        analytics=graph, github=FakeGitHubProvider() if github is _FAKE_GITHUB else github,
        packages=FakePackageProvider() if packages is _FAKE_GITHUB else packages, data_mode="seed",
    )  # fmt: skip
    return Application(service, **kwargs)


def get(app: Application, path: str, query: dict[str, Any] | str = "", **headers: str) -> tuple[Response, Any]:
    qs = query if isinstance(query, str) else urlencode(query, doseq=True)
    response = asyncio.run(app.handle(Request("GET", path, qs, headers)))
    return response, (json.loads(response.body) if response.body else None)


# One representative request per implemented operation. A new route without an entry fails below.
SAMPLES: dict[str, tuple[str, dict[str, Any]]] = {
    "health_live": ("/health/live", {}),
    "health_ready": ("/health/ready", {}),
    "get_meta": ("/api/v1/meta", {}),
    "list_entities": ("/api/v1/entities", {"limit": 5}),
    "get_entity": (f"/api/v1/entities/{PYTHON}", {}),
    "get_entity_by_slug": ("/api/v1/entities/by-slug/language/python", {}),
    "get_entity_neighbors": (f"/api/v1/entities/{NEXT}/neighbors", {"depth": 2}),
    "get_entity_timeline": (f"/api/v1/entities/{PYTHON}/timeline", {}),
    "list_technologies": ("/api/v1/technologies", {"limit": 5}),
    "list_companies": ("/api/v1/companies", {}),
    "search": ("/api/v1/search", {"q": "react"}),
    "graph_overview": ("/api/v1/graph/overview", {}),
    "graph_path": ("/api/v1/graph/path", {"from": PYTHON, "to": NVIDIA}),
    "timeline": ("/api/v1/timeline", {"start_year": 2000, "end_year": 2020}),
    "github_parse": ("/api/v1/github/parse", {"input": "vercel/next.js"}),
    "get_repository_analysis": (f"/api/v1/github/repositories/{FIXTURE_REF.owner}/{FIXTURE_REF.repo}", {}),
    "get_repository_activity": (f"/api/v1/github/repositories/{FIXTURE_REF.owner}/{FIXTURE_REF.repo}/activity", {}),
    "get_commit_activity": (f"/api/v1/github/repositories/{FIXTURE_REF.owner}/{FIXTURE_REF.repo}/commit-activity", {}),
    "get_package_dependencies": ("/api/v1/registries/pypi/package", {"name": "fixture-web"}),
    "get_package_releases": ("/api/v1/registries/pypi/releases", {"name": "fixture-web"}),
    "get_github_rate_limit": ("/api/v1/github/rate-limit", {}),
}


class ContractTest(unittest.TestCase):
    def test_every_implemented_route_has_a_handler_and_a_sample(self) -> None:
        implemented = {r.operation_id for r in ROUTES if r.status is Status.IMPLEMENTED}
        self.assertEqual(implemented, set(HANDLERS))
        self.assertEqual(implemented, set(SAMPLES))

    def test_every_response_matches_its_published_schema(self) -> None:
        app = make_app()
        for operation_id, (path, query) in SAMPLES.items():
            with self.subTest(operation=operation_id):
                response, body = get(app, path, query)
                self.assertEqual(response.status, 200, body)
                schema = COMPONENTS[OPERATIONS[operation_id].response_schema]
                self.assertEqual(validate(body, schema, COMPONENTS), [])

    def test_every_planned_route_answers_501_in_the_error_envelope(self) -> None:
        app = make_app()
        for route in ROUTES:
            if route.status is not Status.PLANNED:
                continue
            path = route.path
            for param in route.path_params:
                path = path.replace("{" + param.name + "}", "x")
            with self.subTest(operation=route.operation_id):
                response = asyncio.run(app.handle(Request(route.method, path)))
                body = json.loads(response.body)
                self.assertEqual(response.status, 501)
                self.assertEqual(body["error"]["code"], "not_implemented")
                self.assertEqual(validate(body, COMPONENTS["Error"], COMPONENTS), [])

    def test_errors_validate_against_the_error_schema(self) -> None:
        app = make_app()
        for path, query, status in [
            ("/api/v1/nope", "", 404),
            ("/api/v1/entities/not-a-uuid", "", 422),
            (f"/api/v1/entities/{entity_id(K.LANGUAGE, 'cobol')}", "", 404),
            ("/api/v1/search", "", 422),
        ]:
            with self.subTest(path=path):
                response, body = get(app, path, query)
                self.assertEqual(response.status, status)
                self.assertEqual(validate(body, COMPONENTS["Error"], COMPONENTS), [])


class HeadersTest(unittest.TestCase):
    def test_security_headers_on_success_and_error(self) -> None:
        app = make_app()
        for path in ("/api/v1/meta", "/api/v1/nope", "/api/v1/search"):
            response, _ = get(app, path)
            for name, value in SECURITY_HEADERS.items():
                self.assertEqual(response.headers[name], value, (path, name))

    def test_errors_are_never_cached(self) -> None:
        response, _ = get(make_app(), "/api/v1/nope")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_health_is_not_cached_but_data_is(self) -> None:
        app = make_app()
        self.assertEqual(get(app, "/health/live")[0].headers["Cache-Control"], "no-store")
        self.assertEqual(get(app, "/api/v1/entities", "limit=1")[0].headers["Cache-Control"], "public, max-age=30")

    def test_etag_and_conditional_get(self) -> None:
        app = make_app()
        first, _ = get(app, "/api/v1/meta")
        etag = first.headers["ETag"]
        again, body = get(app, "/api/v1/meta", **{"if-none-match": etag})
        self.assertEqual((again.status, again.body, body), (304, b"", None))
        self.assertEqual(again.headers["ETag"], etag)
        weak, _ = get(app, "/api/v1/meta", **{"if-none-match": f'W/{etag}, "other"'})
        self.assertEqual(weak.status, 304)
        star, _ = get(app, "/api/v1/meta", **{"if-none-match": "*"})
        self.assertEqual(star.status, 304)
        stale, _ = get(app, "/api/v1/meta", **{"if-none-match": '"stale"'})
        self.assertEqual(stale.status, 200)

    def test_request_id_is_echoed_only_when_safe(self) -> None:
        app = make_app(new_request_id=lambda: "generated-id-0001")
        response = get(app, "/api/v1/meta", **{"x-request-id": "client-abc-12345"})[0]
        self.assertEqual(response.headers["X-Request-ID"], "client-abc-12345")
        for bad in ("short", "has space in it 123", "new\r\nline-injection", "x" * 65, "é" * 10):
            with self.subTest(bad=bad[:12]):
                response, _ = get(app, "/api/v1/meta", **{"x-request-id": bad})
                self.assertEqual(response.headers["X-Request-ID"], "generated-id-0001")

    def test_error_body_carries_the_same_request_id(self) -> None:
        response, body = get(make_app(), "/api/v1/nope", **{"x-request-id": "trace-0123456789"})
        self.assertEqual(body["error"]["request_id"], response.headers["X-Request-ID"])
        self.assertEqual(body["error"]["request_id"], "trace-0123456789")

    def test_json_content_type(self) -> None:
        response, _ = get(make_app(), "/api/v1/meta")
        self.assertEqual(response.headers["Content-Type"], "application/json; charset=utf-8")


class RoutingTest(unittest.TestCase):
    def test_method_not_allowed_lists_allowed_methods(self) -> None:
        app = make_app()
        response = asyncio.run(app.handle(Request("POST", "/api/v1/search")))
        self.assertEqual(response.status, 405)
        self.assertEqual(response.headers["Allow"], "GET")
        response = asyncio.run(app.handle(Request("GET", f"/api/v1/user/bookmarks/{PYTHON}")))
        self.assertEqual(response.status, 405)
        self.assertEqual(response.headers["Allow"], "DELETE, PUT")

    def test_head_and_options_are_not_allowed(self) -> None:
        app = make_app()
        for method in ("HEAD", "OPTIONS", "PATCH"):
            self.assertEqual(asyncio.run(app.handle(Request(method, "/api/v1/meta"))).status, 405)

    def test_unknown_and_odd_paths_are_404(self) -> None:
        app = make_app()
        for path in ("/", "/api", "/api/v1/", "/api/v1/meta/", "/api/v1//meta", "/api/v1/entities/é",
                     "/api/v1/meta\n", "api/v1/meta", "/api/v2/meta", "/../etc/passwd"):  # fmt: skip
            with self.subTest(path=path):
                self.assertEqual(asyncio.run(app.handle(Request("GET", path))).status, 404)

    def test_oversized_body_is_413(self) -> None:
        response = asyncio.run(
            make_app().handle(Request("POST", "/api/v1/research", body=b"x" * (limits.MAX_BODY_BYTES + 1)))
        )
        self.assertEqual(response.status, 413)

    def test_authenticated_route_marked_implemented_fails_closed(self) -> None:
        from dataclasses import replace

        import vyron_api.api.app as app_module

        route = OPERATIONS["get_me"]
        leaked = replace(route, status=Status.IMPLEMENTED)
        app = make_app()
        original = app_module.find_route
        app_module.find_route = lambda method, path: (leaked, {})
        try:
            response = asyncio.run(app.handle(Request("GET", route.path)))
        finally:
            app_module.find_route = original
        self.assertEqual(response.status, 401)


class RateLimitTest(unittest.TestCase):
    def test_429_with_retry_after_and_headers(self) -> None:
        limits_map = dict(DEFAULT_LIMITS)
        limits_map[RateBucket.READ] = Limit(2, 60)
        app = make_app(rate_limiter=FixedWindowRateLimiter(limits_map, now=lambda: 0.0))
        first, _ = get(app, "/api/v1/meta")
        self.assertEqual(first.headers["RateLimit-Limit"], "2")
        self.assertEqual(first.headers["RateLimit-Remaining"], "1")
        get(app, "/api/v1/meta")
        blocked, body = get(app, "/api/v1/meta")
        self.assertEqual(blocked.status, 429)
        self.assertEqual(body["error"]["code"], "rate_limited")
        self.assertEqual(blocked.headers["Retry-After"], "60")
        self.assertEqual(blocked.headers["RateLimit-Remaining"], "0")

    def test_buckets_do_not_starve_each_other(self) -> None:
        limits_map = dict(DEFAULT_LIMITS)
        limits_map[RateBucket.SEARCH] = Limit(1, 60)
        app = make_app(rate_limiter=FixedWindowRateLimiter(limits_map, now=lambda: 0.0))
        self.assertEqual(get(app, "/api/v1/search", "q=a")[0].status, 200)
        self.assertEqual(get(app, "/api/v1/search", "q=a")[0].status, 429)
        self.assertEqual(get(app, "/api/v1/meta")[0].status, 200)
        self.assertEqual(get(app, "/health/live")[0].status, 200)

    def test_clients_are_separated_by_ip(self) -> None:
        limits_map = dict(DEFAULT_LIMITS)
        limits_map[RateBucket.READ] = Limit(1, 60)
        app = make_app(rate_limiter=FixedWindowRateLimiter(limits_map, now=lambda: 0.0))
        statuses = [
            asyncio.run(app.handle(Request("GET", "/api/v1/meta", client_ip=ip))).status
            for ip in ("1.1.1.1", "2.2.2.2", "1.1.1.1")
        ]
        self.assertEqual(statuses, [200, 200, 429])

    def test_rate_limit_applies_before_validation_work(self) -> None:
        limits_map = dict(DEFAULT_LIMITS)
        limits_map[RateBucket.SEARCH] = Limit(1, 60)
        app = make_app(rate_limiter=FixedWindowRateLimiter(limits_map, now=lambda: 0.0))
        get(app, "/api/v1/search", "q=a")
        self.assertEqual(get(app, "/api/v1/search", "limit=bad")[0].status, 429)


class PaginationOverHttpTest(unittest.TestCase):
    def test_following_cursors_visits_every_entity_once(self) -> None:
        app = make_app()
        seen: list[str] = []
        query: dict[str, Any] = {"limit": 4}
        pages = 0
        while True:
            response, body = get(app, "/api/v1/entities", query)
            self.assertEqual(response.status, 200, body)
            seen.extend(item["name"] for item in body["items"])
            pages += 1
            cursor = body["page"]["next_cursor"]
            if cursor is None:
                break
            query = {"limit": 4, "cursor": cursor}
        self.assertEqual(len(seen), 60)
        self.assertEqual(len(set(seen)), 60)
        self.assertEqual(seen, sorted(seen, key=str.casefold))
        self.assertEqual(pages, 15)

    def test_cursor_cannot_be_reused_with_other_filters_or_endpoints(self) -> None:
        app = make_app()
        _, body = get(app, "/api/v1/entities", {"limit": 2})
        cursor = body["page"]["next_cursor"]
        for path, query in [
            ("/api/v1/entities", {"limit": 2, "kind": "language", "cursor": cursor}),
            ("/api/v1/technologies", {"limit": 2, "cursor": cursor}),
            ("/api/v1/search", {"q": "a", "cursor": cursor}),
        ]:
            with self.subTest(path=path):
                response, error = get(app, path, query)
                self.assertEqual(response.status, 422)
                self.assertEqual(error["error"]["details"][0]["field"], "cursor")

    def test_forged_cursor_is_rejected(self) -> None:
        response, body = get(make_app(), "/api/v1/entities", {"cursor": "AAAA.BBBB"})
        self.assertEqual((response.status, body["error"]["details"][0]["field"]), (422, "cursor"))

    def test_timeline_pages_cover_every_event_once_and_bind_their_filters(self) -> None:
        app = make_app()
        seen: list[str] = []
        query: dict[str, Any] = {"limit": 3}
        while True:
            response, body = get(app, "/api/v1/timeline", query)
            self.assertEqual(response.status, 200, body)
            self.assertEqual(body["page"]["total"], 41)
            seen.extend(e["id"] for e in body["items"])
            if body["page"]["next_cursor"] is None:
                break
            query = {"limit": 3, "cursor": body["page"]["next_cursor"]}
        self.assertEqual(len(seen), 41)
        self.assertEqual(len(set(seen)), 41)
        _, first = get(app, "/api/v1/timeline", {"limit": 3})
        cursor = first["page"]["next_cursor"]
        response, error = get(app, "/api/v1/timeline", {"limit": 3, "cursor": cursor, "start_year": 2000})
        self.assertEqual((response.status, error["error"]["details"][0]["field"]), (422, "cursor"))
        response, _ = get(app, f"/api/v1/entities/{PYTHON}/timeline", {"cursor": cursor})
        self.assertEqual(response.status, 422)

    def test_dense_neighbourhoods_are_capped_and_flagged(self) -> None:
        hub = Entity(entity_id(K.TECHNOLOGY, "hub"), K.TECHNOLOGY, "hub", "Hub", Provenance.SEED)
        spokes = [
            Entity(entity_id(K.TECHNOLOGY, f"s{i}"), K.TECHNOLOGY, f"s{i}", f"S{i}", Provenance.SEED) for i in range(60)
        ]
        graph = InMemoryGraph([hub, *spokes])
        for i, spoke in enumerate(spokes):
            graph.add_relationship(Relationship(hub.id, spoke.id, RelationType.RELATED_TO, Provenance.SEED))
            for other in spokes[i + 1 :]:
                graph.add_relationship(Relationship(spoke.id, other.id, RelationType.RELATED_TO, Provenance.SEED))
        self.assertGreater(graph.relationship_count, limits.MAX_GRAPH_EDGES)
        response, body = get(make_app(graph), f"/api/v1/entities/{hub.id}/neighbors", {"max_nodes": 300})
        self.assertEqual(response.status, 200)
        self.assertEqual(len(body["relationships"]), limits.MAX_GRAPH_EDGES)
        self.assertTrue(body["truncated"])
        self.assertEqual(validate(body, COMPONENTS["Subgraph"], COMPONENTS), [])

    def test_last_page_has_no_cursor(self) -> None:
        _, body = get(make_app(), "/api/v1/entities", {"limit": 100})
        self.assertIsNone(body["page"]["next_cursor"])
        self.assertEqual(body["page"]["total"], 60)


class BehaviourTest(unittest.TestCase):
    def test_seed_data_is_always_labelled(self) -> None:
        app = make_app()
        for operation_id in ("list_entities", "get_entity_neighbors", "graph_overview", "search"):
            path, query = SAMPLES[operation_id]
            _, body = get(app, path, query)
            self.assertEqual(body["data_notice"], SEED_NOTICE, operation_id)
        _, body = get(app, "/api/v1/entities/" + PYTHON)
        self.assertEqual(body["provenance"], "seed")
        _, meta = get(app, "/api/v1/meta")
        self.assertEqual((meta["data_mode"], meta["data_notice"]), ("seed", SEED_NOTICE))

    def test_search_is_typo_and_punctuation_tolerant(self) -> None:
        app = make_app()
        cases = [("nextjs", "next-js"), ("Next.js", "next-js"), ("pytorhc", "pytorch"), ("k8s", "kubernetes")]
        for query, expected in cases:
            _, body = get(app, "/api/v1/search", {"q": query})
            self.assertEqual(body["items"][0]["slug"], expected, query)

    def test_search_kind_filter_and_empty_results(self) -> None:
        app = make_app()
        _, body = get(app, "/api/v1/search", {"q": "p", "kind": "company"})
        self.assertTrue(all(item["kind"] == "company" for item in body["items"]))
        _, body = get(app, "/api/v1/search", {"q": "zzzzqqqq"})
        self.assertEqual((body["items"], body["page"]["total"]), ([], 0))
        response, _ = get(app, "/api/v1/search", {"q": "   "})
        self.assertEqual(response.status, 422)

    def test_technology_and_company_collections_are_partitioned(self) -> None:
        app = make_app()
        _, tech = get(app, "/api/v1/technologies", {"limit": 100})
        _, companies = get(app, "/api/v1/companies", {"limit": 100})
        self.assertIn("nvidia", {e["slug"] for e in companies["items"]})
        self.assertIn("vercel", {e["slug"] for e in companies["items"]})
        self.assertEqual(companies["page"]["total"], 11)
        self.assertEqual(tech["page"]["total"], 49)
        self.assertFalse({e["id"] for e in tech["items"]} & {e["id"] for e in companies["items"]})
        response, error = get(app, "/api/v1/technologies", {"kind": "company"})
        self.assertEqual(response.status, 422)
        self.assertEqual(error["error"]["details"][0]["field"], "kind")
        _, narrowed = get(app, "/api/v1/technologies", {"kind": "language", "limit": 100})
        self.assertEqual({e["kind"] for e in narrowed["items"]}, {"language"})

    def test_entity_lookup_by_id_and_slug_agree(self) -> None:
        app = make_app()
        _, by_id = get(app, f"/api/v1/entities/{PYTHON}")
        _, by_slug = get(app, "/api/v1/entities/by-slug/language/python")
        self.assertEqual(by_id, by_slug)
        self.assertEqual(get(app, "/api/v1/entities/by-slug/framework/python")[0].status, 404)

    def test_neighbors(self) -> None:
        app = make_app()
        _, depth0 = get(app, f"/api/v1/entities/{NEXT}/neighbors", {"depth": 0})
        self.assertEqual(len(depth0["entities"]), 1)
        _, depth1 = get(app, f"/api/v1/entities/{NEXT}/neighbors", {"depth": 1})
        self.assertEqual({e["slug"] for e in depth1["entities"]}, {"next-js", "react", "node-js", "vercel"})
        self.assertFalse(depth1["truncated"])
        _, capped = get(app, f"/api/v1/entities/{NEXT}/neighbors", {"depth": 3, "max_nodes": 3})
        self.assertEqual(len(capped["entities"]), 3)
        self.assertTrue(capped["truncated"])
        _, filtered = get(app, f"/api/v1/entities/{NEXT}/neighbors", {"relation_type": "BUILT_ON"})
        self.assertEqual({e["slug"] for e in filtered["entities"]}, {"next-js", "react"})
        self.assertEqual(get(app, f"/api/v1/entities/{entity_id(K.LANGUAGE, 'cobol')}/neighbors")[0].status, 404)

    def test_path(self) -> None:
        app = make_app()
        _, found = get(app, "/api/v1/graph/path", {"from": PYTHON, "to": NVIDIA})
        self.assertTrue(found["found"])
        self.assertEqual((found["entity_ids"][0], found["entity_ids"][-1]), (PYTHON, NVIDIA))
        self.assertEqual(len(found["entity_ids"]), 4)  # python - (pytorch|tensorflow) - cuda - nvidia
        self.assertEqual(len(found["relationships"]), 3)
        _, via_vercel = get(app, "/api/v1/graph/path", {"from": REACT, "to": entity_id(K.PLATFORM, "github")})
        self.assertEqual(len(via_vercel["entity_ids"]), 4)  # react - next.js - vercel - github
        _, apart = get(app, "/api/v1/graph/path", {"from": REACT, "to": entity_id(K.COMPANY, "apple")})
        self.assertEqual((apart["found"], apart["entity_ids"], apart["relationships"]), (False, [], []))
        _, short = get(app, "/api/v1/graph/path", {"from": PYTHON, "to": NVIDIA, "max_depth": 2})
        self.assertFalse(short["found"])
        self.assertEqual(get(app, "/api/v1/graph/path", {"from": REACT, "to": REACT})[0].status, 422)
        self.assertEqual(get(app, "/api/v1/graph/path", {"from": REACT})[0].status, 422)
        missing = entity_id(K.LANGUAGE, "cobol")
        self.assertEqual(get(app, "/api/v1/graph/path", {"from": REACT, "to": missing})[0].status, 404)

    def test_timeline_and_predictions(self) -> None:
        app = make_app()
        _, window = get(app, "/api/v1/timeline", {"start_year": 2007, "end_year": 2009})
        titles = [e["title"] for e in window["items"]]
        self.assertEqual(
            titles,
            [
                "CUDA released",
                "GitHub launched",
                "Go language announced",
                "Redis released",
                "MongoDB 1.0 released",
                "Node.js released",
            ],
        )
        years = [e["occurred_on"] for e in window["items"]]
        self.assertEqual(years, sorted(years))
        self.assertTrue(all(not e["is_prediction"] for e in window["items"]))
        self.assertEqual(get(app, "/api/v1/timeline", {"start_year": 2020, "end_year": 2010})[0].status, 422)
        self.assertEqual(get(app, "/api/v1/timeline", {"start_year": 1500})[0].status, 422)
        _, entity = get(app, f"/api/v1/entities/{PYTHON}/timeline")
        self.assertEqual([e["title"] for e in entity["items"]], ["Python first released"])

    def test_graph_overview(self) -> None:
        _, body = get(make_app(), "/api/v1/graph/overview", {"top": 3, "clusters": 2})
        self.assertEqual((body["entity_count"], body["relationship_count"]), (60, 68))
        self.assertEqual(len(body["top_entities"]), 3)
        self.assertEqual(len(body["clusters"]), 2)
        for cluster in body["clusters"]:
            self.assertEqual(len(cluster["member_ids"]), cluster["size"])
            self.assertTrue(all(isinstance(member, str) for member in cluster["member_ids"]))
        self.assertEqual(body["score_provenance"], "estimated")
        scores = [t["score"] for t in body["top_entities"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_github_parse_is_pure_and_strict(self) -> None:
        app = make_app()
        _, ok = get(app, "/api/v1/github/parse", {"input": "https://github.com/vercel/next.js/pull/42"})
        self.assertEqual(
            (ok["kind"], ok["number"], ok["html_url"]), ("pull_request", 42, "https://github.com/vercel/next.js")
        )
        for bad in ("https://evil.example/a/b", "http://localhost:8080/a/b", "https://user:pw@github.com/a/b",
                    "git@evil.com:a/b.git", "a/b/c", "https://github.com/a/b%2F..%2Fc"):  # fmt: skip
            with self.subTest(bad=bad):
                response, body = get(app, "/api/v1/github/parse", {"input": bad})
                self.assertEqual(response.status, 422)
                self.assertEqual(body["error"]["code"], "validation_error")


ANALYSIS = f"/api/v1/github/repositories/{FIXTURE_REF.owner}/{FIXTURE_REF.repo}"


class GitHubRoutesTest(unittest.TestCase):
    """The two routes that read live GitHub data. The provider here is a fixture, so these show how the API
    behaves given a provider's answers, not what GitHub returns."""

    def test_the_analysis_carries_its_own_evidence_and_age(self) -> None:
        response, body = get(make_app(), ANALYSIS)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["full_name"], "fixture-org/example-repo")
        self.assertEqual(body["html_url"], "https://github.com/fixture-org/example-repo")
        self.assertEqual(body["provenance"], Provenance.SOURCED.value)
        self.assertEqual(body["cache_status"], "fresh")
        self.assertEqual(body["fetched_at"], "2026-10-21T10:15:30Z")
        self.assertEqual(
            body["source_urls"],
            [
                "https://api.github.com/repos/fixture-org/example-repo",
                "https://api.github.com/repos/fixture-org/example-repo/languages",
            ],
        )
        self.assertEqual(sum(body["languages"].values()), 10_000)

    def test_the_analysis_may_be_cached_for_a_minute_but_failures_never_are(self) -> None:
        ok, _ = get(make_app(), ANALYSIS)
        self.assertEqual(ok.headers["Cache-Control"], "public, max-age=60")
        self.assertIn("ETag", ok.headers)
        failing = make_app(github=FakeGitHubProvider(fail_with=GitHubNotFoundError("x")))
        missing, _ = get(failing, ANALYSIS)
        self.assertEqual(missing.headers["Cache-Control"], "no-store")

    def test_the_names_go_to_the_provider_as_two_validated_parts(self) -> None:
        provider = FakeGitHubProvider()
        get(make_app(github=provider), "/api/v1/github/repositories/Fixture-Org/example.repo_1")
        self.assertEqual([ref.full_name for ref in provider.requested], ["Fixture-Org/example.repo_1"])

    def test_names_that_are_not_repositories_never_reach_the_provider(self) -> None:
        provider = FakeGitHubProvider()
        app = make_app(github=provider)
        for owner, repo in [
            ("-bad", "repo"), ("bad-", "repo"), ("a" * 40, "repo"), ("under_score", "repo"),
            ("vercel", ".."), ("vercel", "."), ("vercel", "a:b"), ("vercel", "a@b"), ("orgs", "repo"),
            ("github.com", "vercel"),
        ]:  # fmt: skip
            with self.subTest(owner=owner, repo=repo):
                response, body = get(app, f"/api/v1/github/repositories/{owner}/{repo}")
                self.assertEqual((response.status, body["error"]["code"]), (422, "validation_error"))
        self.assertEqual(provider.requested, [])

    def test_a_longer_path_is_not_a_route(self) -> None:
        provider = FakeGitHubProvider()
        for path in (f"{ANALYSIS}/issues", f"{ANALYSIS}/", "/api/v1/github/repositories/fixture-org"):
            with self.subTest(path=path):
                self.assertEqual(get(make_app(github=provider), path)[0].status, 404)
        self.assertEqual(provider.requested, [])

    def test_the_rate_limit_route_reports_the_quota_uncached(self) -> None:
        response, body = get(make_app(), "/api/v1/github/rate-limit")
        self.assertEqual(response.status, 200)
        self.assertEqual(
            body, {"limit": 60, "remaining": 58, "used": 2, "reset_at": "2026-10-21T10:53:20Z", "resource": "core"}
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_upstream_failures_map_to_distinct_statuses_without_upstream_text(self) -> None:
        upstream = "No linked GitHub account. Connect your GitHub account and retry. token=ghp_SECRET"
        cases = [
            (GitHubNotFoundError(upstream), 404, "not_found"),
            (GitHubRateLimitedError(upstream, 90.0), 503, "upstream_rate_limited"),
            (GitHubAuthError(upstream), 502, "upstream_error"),
            (GitHubForbiddenError(upstream), 502, "upstream_error"),
            (GitHubUnavailableError(upstream), 502, "upstream_error"),
            (MalformedGitHubPayloadError(upstream), 502, "upstream_error"),
        ]
        for exc, status, code in cases:
            for path in (ANALYSIS, "/api/v1/github/rate-limit"):
                with self.subTest(exc=type(exc).__name__, path=path):
                    app = make_app(github=FakeGitHubProvider(fail_with=exc))
                    with self.assertLogs("vyron.api", level="INFO"):
                        response, body = get(app, path)
                    self.assertEqual((response.status, body["error"]["code"]), (status, code))
                    self.assertEqual(validate(body, COMPONENTS["Error"], COMPONENTS), [])
                    for fragment in ("ghp_SECRET", "linked", "account"):
                        self.assertNotIn(fragment, response.body.decode())

    def test_a_rate_limited_upstream_tells_the_caller_when_to_retry(self) -> None:
        app = make_app(github=FakeGitHubProvider(fail_with=GitHubRateLimitedError("x", 90.0)))
        response, body = get(app, ANALYSIS)
        self.assertEqual(response.headers["Retry-After"], "91")
        self.assertEqual(body["error"]["code"], "upstream_rate_limited")

    def test_an_upstream_failure_is_logged_for_the_operator_with_the_request_id(self) -> None:
        app = make_app(github=FakeGitHubProvider(fail_with=GitHubForbiddenError("blocked by gateway")))
        with self.assertLogs("vyron.api", level="WARNING") as logs:
            get(app, ANALYSIS, **{"x-request-id": "trace-0123456789"})
        warning = logs.records[0]
        self.assertEqual((warning.request_id, warning.operation_id), ("trace-0123456789", "get_repository_analysis"))
        self.assertIn("blocked by gateway", warning.getMessage())  # the operator sees it; the caller does not

    def test_with_github_switched_off_both_routes_say_so_and_parsing_still_works(self) -> None:
        app = make_app(github=None)
        for path in (ANALYSIS, "/api/v1/github/rate-limit"):
            with self.subTest(path=path):
                response, body = get(app, path)
                self.assertEqual((response.status, body["error"]["code"]), (503, "service_unavailable"))
                self.assertIn("switched off", body["error"]["message"])
        self.assertEqual(get(app, "/api/v1/github/parse", {"input": "vercel/next.js"})[0].status, 200)

    def test_the_github_routes_have_their_own_tighter_rate_limit(self) -> None:
        limits_map = {**DEFAULT_LIMITS}
        app = make_app(rate_limiter=FixedWindowRateLimiter(limits_map, now=lambda: 0.0))
        statuses = [get(app, ANALYSIS)[0].status for _ in range(31)]
        self.assertEqual((statuses.count(200), statuses[-1]), (30, 429))


class HostCheckTest(unittest.TestCase):
    def test_a_loopback_only_server_refuses_every_other_host(self) -> None:
        app = make_app(allowed_hosts=LOOPBACK_HOSTS)
        for host in ("localhost:8000", "127.0.0.1", "127.0.0.1:8000", "LOCALHOST:8000", "[::1]:8000"):
            with self.subTest(host=host):
                self.assertEqual(get(app, "/api/v1/meta", host=host)[0].status, 200)
        for host in (
            "evil.example", "evil.example:8000", "localhost.evil.example", "127.0.0.1.evil.example",
            "localhost@evil.example", "localhost:8000:1", "localhost:abc", "", "::1", "[::1", "localhost.",
        ):  # fmt: skip
            with self.subTest(host=host):
                response, body = get(app, "/api/v1/meta", host=host)
                self.assertEqual((response.status, body["error"]["code"]), (403, "forbidden"))
                self.assertEqual(validate(body, COMPONENTS["Error"], COMPONENTS), [])

    def test_a_request_without_a_host_is_refused_by_a_loopback_only_server(self) -> None:
        response, body = get(make_app(allowed_hosts=LOOPBACK_HOSTS), "/api/v1/meta")
        self.assertEqual((response.status, body["error"]["code"]), (403, "forbidden"))

    def test_the_refusal_comes_before_any_work_including_github_calls(self) -> None:
        provider = FakeGitHubProvider()
        app = make_app(github=provider, allowed_hosts=LOOPBACK_HOSTS)
        response, _ = get(app, ANALYSIS, host="evil.example")
        self.assertEqual(response.status, 403)
        self.assertEqual(provider.requested, [])

    def test_without_a_list_any_host_is_answered(self) -> None:
        self.assertEqual(get(make_app(), "/api/v1/meta", host="anything.example")[0].status, 200)
        self.assertEqual(get(make_app(), "/api/v1/meta")[0].status, 200)

    def test_a_deployment_can_name_its_own_hosts(self) -> None:
        app = make_app(allowed_hosts={"Vyron.Example.COM"})
        self.assertEqual(get(app, "/api/v1/meta", host="vyron.example.com:443")[0].status, 200)
        self.assertEqual(get(app, "/api/v1/meta", host="localhost")[0].status, 403)


class FailureHandlingTest(unittest.TestCase):
    def test_unavailable_graph_gives_503_and_degraded_readiness(self) -> None:
        class Down(InMemoryGraph):
            async def list_entities(self, **kwargs: Any):
                raise GraphUnavailableError("bolt://neo4j:7687 refused password=hunter2")

        app = make_app(Down(*(lambda g: (g.entities(), g.relationships()))(build_seed_graph())))
        response, body = get(app, "/health/ready")
        self.assertEqual(response.status, 503)
        self.assertEqual(body, {"status": "degraded", "checks": {"graph": "unavailable", "analytics": "ok"}})
        response, error = get(app, "/api/v1/entities")
        self.assertEqual((response.status, error["error"]["code"]), (503, "service_unavailable"))
        self.assertNotIn("hunter2", response.body.decode())
        self.assertEqual(get(app, "/health/live")[0].status, 200)  # liveness never depends on backends

    def test_unexpected_errors_are_500_without_leaking_and_are_logged(self) -> None:
        class Broken(InMemoryGraph):
            async def search(self, *args: Any, **kwargs: Any):
                raise RuntimeError("SELECT * FROM users WHERE password = 'hunter2'")

        seed = build_seed_graph()
        app = make_app(Broken(seed.entities(), seed.relationships()))
        with self.assertLogs("vyron.api", level="ERROR") as logs:
            response, body = get(app, "/api/v1/search", {"q": "x"}, **{"x-request-id": "trace-0123456789"})
        self.assertEqual((response.status, body["error"]["code"]), (500, "internal_error"))
        self.assertNotIn("hunter2", response.body.decode())
        self.assertNotIn("SELECT", response.body.decode())
        self.assertEqual(logs.records[0].request_id, "trace-0123456789")
        self.assertEqual(logs.records[0].operation_id, "search")

    def test_request_logs_do_not_include_query_text(self) -> None:
        with self.assertLogs("vyron.api", level=logging.INFO) as logs:
            get(make_app(), "/api/v1/search", {"q": "secret-search-term"})
        rendered = " ".join(str(record.__dict__) for record in logs.records)
        self.assertNotIn("secret-search-term", rendered)


if __name__ == "__main__":
    unittest.main()
