"""npm driver and its graph conversion. Fixtures are hand-written from the shape of npm's answer."""

import json
import unittest
from datetime import UTC, datetime

from registry_fakes import FakeTransport
from vyron_api.adapters.memory_graph import InMemoryGraph
from vyron_api.domain.entities import EntityKind, RelationType
from vyron_api.domain.provenance import Provenance
from vyron_api.registries.errors import InvalidPackageNameError, PackageNotFoundError, RegistryError
from vyron_api.registries.npm import NpmPackage, fetch_npm_package, npm_package_to_graph, validate_npm_name
from vyron_api.registries.service import RegistryIngestService

EXPRESS_URL = "https://registry.npmjs.org/express"


def document(name: str = "express", latest: str = "4.19.2", **version: object) -> bytes:
    version_data = {"name": name, "version": latest, "description": "Fast, unopinionated, minimalist web framework",
                    "license": "MIT", "homepage": "http://expressjs.com/",
                    "dependencies": {"accepts": "~1.3.8", "body-parser": "1.20.2", "cookie": "0.6.0", "debug": "2.6.9"},
                    **version}  # fmt: skip
    return json.dumps({"name": name, "dist-tags": {"latest": latest}, "versions": {latest: version_data}}).encode()


class Names(unittest.TestCase):
    def test_ordinary_scoped_and_legacy_names_are_accepted(self) -> None:
        for name in ("express", "@types/node", "left-pad", "JSONStream", "lodash.merge", "@scope/pkg.name", "a"):
            with self.subTest(name=name):
                self.assertEqual(validate_npm_name(name), name)

    def test_names_that_could_steer_the_request_are_refused(self) -> None:
        bad = ["", "../x", "a/b", "@scope", "@scope/", "@/x", "x?y=1", "x#y", "a b", "a\nb", "%2e%2e", "express/latest",
               "é", "x" * 215, "@a/b/c"]  # fmt: skip
        for name in bad:
            with self.subTest(name=name), self.assertRaises(InvalidPackageNameError):
                validate_npm_name(name)

    def test_a_refused_name_makes_no_request(self) -> None:
        transport = FakeTransport({})
        with self.assertRaises(InvalidPackageNameError):
            fetch_npm_package("../x", transport=transport)  # type: ignore[arg-type]
        self.assertEqual(transport.requested_urls, [])


class Fetch(unittest.TestCase):
    def test_success(self) -> None:
        transport = FakeTransport({EXPRESS_URL: document()})
        pkg = fetch_npm_package("express", transport=transport)  # type: ignore[arg-type]
        self.assertEqual((pkg.name, pkg.slug, pkg.version, pkg.license_spdx), ("express", "express", "4.19.2", "MIT"))
        self.assertEqual(pkg.homepage, "http://expressjs.com/")
        self.assertEqual(pkg.dependencies, ("accepts", "body-parser", "cookie", "debug"))
        self.assertEqual(pkg.source_url, EXPRESS_URL)

    def test_scoped_names_are_encoded_the_way_the_registry_expects(self) -> None:
        url = "https://registry.npmjs.org/@types%2Fnode"
        body = document(name="@types/node", latest="20.12.7", dependencies={"undici-types": "~5.26.4"})
        transport = FakeTransport({url: body})
        pkg = fetch_npm_package("@types/node", transport=transport)  # type: ignore[arg-type]
        self.assertEqual((pkg.name, pkg.slug, pkg.dependencies), ("@types/node", "types-node", ("undici-types",)))
        self.assertEqual(transport.requested_urls, [url])

    def test_only_runtime_dependencies_of_the_latest_tag_are_read(self) -> None:
        body = document(devDependencies={"mocha": "1"}, peerDependencies={"react": "18"},
                        optionalDependencies={"fsevents": "2"})  # fmt: skip
        pkg = fetch_npm_package("express", transport=FakeTransport({EXPRESS_URL: body}))  # type: ignore[arg-type]
        self.assertEqual(pkg.dependencies, ("accepts", "body-parser", "cookie", "debug"))

    def test_no_dependencies_is_not_an_error(self) -> None:
        empty: dict[str, str] = {}
        for value in (None, empty):
            body = document(dependencies=value)
            pkg = fetch_npm_package("express", transport=FakeTransport({EXPRESS_URL: body}))  # type: ignore[arg-type]
            self.assertEqual(pkg.dependencies, ())

    def test_license_given_as_an_object_is_read(self) -> None:
        body = document(license={"type": "ISC", "url": "x"})
        pkg = fetch_npm_package("express", transport=FakeTransport({EXPRESS_URL: body}))  # type: ignore[arg-type]
        self.assertEqual(pkg.license_spdx, "ISC")

    def test_not_found(self) -> None:
        with self.assertRaises(PackageNotFoundError):
            fetch_npm_package("nonexistent-pkg-9999", transport=FakeTransport({}))  # type: ignore[arg-type]

    def test_answers_of_the_wrong_shape_are_refused_not_crashed_on(self) -> None:
        wrong = [b"[]", b'{"name": "express"}', b'{"dist-tags": {"latest": "1"}, "versions": {}}',
                 b'{"dist-tags": [], "versions": {}}', b'{"dist-tags": {"latest": 1}, "versions": {"1": {}}}',
                 json.dumps({"dist-tags": {"latest": "1"}, "versions": {"1": {"dependencies": ["a"]}}}).encode(),
                 json.dumps({"dist-tags": {"latest": "1"}, "versions": {"1": []}}).encode()]  # fmt: skip
        for body in wrong:
            with self.subTest(body=body[:50]), self.assertRaises(RegistryError):
                fetch_npm_package("express", transport=FakeTransport({EXPRESS_URL: body}))  # type: ignore[arg-type]


class Graph(unittest.TestCase):
    def package(self, **overrides: object) -> NpmPackage:
        fields: dict[str, object] = dict(
            name="express", slug="express", version="4.19.2", description="Fast, minimalist web framework",
            license_spdx="MIT", homepage="http://expressjs.com/", dependencies=("accepts", "cookie"),
            source_url=EXPRESS_URL,
        )  # fmt: skip
        fields.update(overrides)
        return NpmPackage(**fields)  # type: ignore[arg-type]

    def test_structure(self) -> None:
        now = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
        main, source, ev, deps, rels = npm_package_to_graph(self.package(), retrieved_at=now, kind=EntityKind.FRAMEWORK)
        self.assertEqual((main.kind, main.slug, main.provenance), (EntityKind.FRAMEWORK, "express", Provenance.SOURCED))
        self.assertEqual(source.url, EXPRESS_URL)
        self.assertIn("express v4.19.2 on npm declares 2 runtime dependencies", ev.claim)
        self.assertIn("development, peer and optional dependencies are not recorded", ev.claim)
        self.assertEqual(ev.locator, "versions.4.19.2.dependencies")
        self.assertEqual((len(deps), len(rels)), (2, 2))
        for rel in rels:
            self.assertEqual(rel.source_id, main.id)
            self.assertEqual((rel.type, rel.provenance), (RelationType.DEPENDS_ON, Provenance.SOURCED))
            self.assertEqual(rel.evidence_ids, (ev.id,))

    def test_ingest_service(self) -> None:
        pkg = self.package(name="react", slug="react", version="18.3.1", dependencies=("loose-envify",),
                           source_url="https://registry.npmjs.org/react")  # fmt: skip
        graph = InMemoryGraph()
        result = RegistryIngestService(graph).ingest_npm("react", pkg=pkg)
        self.assertEqual((result.new_entities_count, result.new_relationships_count), (2, 1))
        self.assertEqual((graph.entity_count, graph.relationship_count), (2, 1))


if __name__ == "__main__":
    unittest.main()
