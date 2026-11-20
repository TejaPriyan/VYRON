"""PyPI driver and its graph conversion. Fixtures are hand-written from the shape of PyPI's answer."""

import json
import unittest
from datetime import UTC, datetime

from registry_fakes import FakeTransport
from vyron_api.adapters.memory_graph import InMemoryGraph
from vyron_api.domain.entities import EntityKind, RelationType
from vyron_api.domain.provenance import Provenance
from vyron_api.registries.errors import InvalidPackageNameError, PackageNotFoundError, RegistryError
from vyron_api.registries.pypi import (
    PyPIPackage,
    fetch_pypi_package,
    normalize_pypi_name,
    parse_dependency_name,
    parse_requirement,
    pypi_package_to_graph,
    slugify,
)
from vyron_api.registries.service import RegistryIngestService

FLASK_URL = "https://pypi.org/pypi/flask/json"
# The shape of Flask's declared requirements as the owner's live run listed them: seven names, one of which
# (importlib-metadata) applies only to old Pythons, plus two extras.
FLASK_REQUIRES = [
    "Werkzeug>=3.1.0",
    "Jinja2>=3.1.2",
    "itsdangerous>=2.2.0",
    "click>=8.1.3",
    "blinker>=1.9.0",
    'importlib-metadata>=3.6.0; python_version < "3.10"',
    "MarkupSafe>=2.1.1",
    'asgiref>=3.2; extra == "async"',
    'python-dotenv; extra == "dotenv"',
]


def flask_body(**info: object) -> bytes:
    base = {"name": "Flask", "version": "3.1.3", "summary": "A web framework.", "license": "BSD-3-Clause",
            "author": "Pallets", "home_page": None, "requires_dist": FLASK_REQUIRES}  # fmt: skip
    return json.dumps({"info": {**base, **info}}).encode("utf-8")


class Names(unittest.TestCase):
    def test_slugify(self) -> None:
        self.assertEqual(slugify("Flask"), "flask")
        self.assertEqual(slugify("scikit-learn"), "scikit-learn")
        self.assertEqual(slugify("Flask_SQLAlchemy"), "flask-sqlalchemy")
        self.assertEqual(slugify("@types/node"), "types-node")
        with self.assertRaises(ValueError):
            slugify("---")

    def test_names_are_put_in_pypis_canonical_form(self) -> None:
        self.assertEqual(normalize_pypi_name("Flask"), "flask")
        self.assertEqual(normalize_pypi_name("Flask_SQLAlchemy"), "flask-sqlalchemy")
        self.assertEqual(normalize_pypi_name("zope.interface"), "zope-interface")
        self.assertEqual(normalize_pypi_name("a"), "a")

    def test_names_that_could_steer_the_request_are_refused(self) -> None:
        bad = ["", " ", "../simple/flask", "flask/json", "flask?x=", "x#frag", "a b", "fl\nask", "%2e%2e", "-flask",
               "flask-", "flask\x00", "é", "a" * 300]  # fmt: skip
        for name in bad:
            with self.subTest(name=name), self.assertRaises(InvalidPackageNameError):
                normalize_pypi_name(name)

    def test_a_refused_name_makes_no_request(self) -> None:
        transport = FakeTransport({})
        with self.assertRaises(InvalidPackageNameError):
            fetch_pypi_package("../simple/flask", transport=transport)  # type: ignore[arg-type]
        self.assertEqual(transport.requested_urls, [])

    def test_the_error_is_both_a_registry_error_and_a_value_error(self) -> None:
        with self.assertRaises(ValueError):
            normalize_pypi_name("a b")
        with self.assertRaises(RegistryError):
            normalize_pypi_name("a b")


class Requirements(unittest.TestCase):
    def test_parse_dependency_name(self) -> None:
        self.assertEqual(parse_dependency_name("requests >= 2.20.0"), "requests")
        self.assertEqual(parse_dependency_name("urllib3<3,>=1.21.1"), "urllib3")
        self.assertEqual(parse_dependency_name("Werkzeug>=3.0.0"), "werkzeug")
        self.assertIsNone(parse_dependency_name("cryptography (>=1.3.4) ; extra == 'security'"))
        self.assertEqual(
            parse_dependency_name("cryptography (>=1.3.4) ; extra == 'security'", include_extras=True),
            "cryptography",
        )

    def test_a_marker_makes_a_requirement_conditional(self) -> None:
        self.assertEqual(parse_requirement("click>=8.1.3"), ("click", False))
        old_python = 'importlib-metadata>=3.6; python_version < "3.10"'
        self.assertEqual(parse_requirement(old_python), ("importlib-metadata", True))
        self.assertEqual(parse_requirement('colorama; platform_system == "Windows"'), ("colorama", True))
        self.assertEqual(parse_requirement("asgiref>=3.2; extra == 'async'"), ("asgiref", True))
        self.assertEqual(parse_requirement("???"), (None, False))


class Fetch(unittest.TestCase):
    def test_only_requirements_without_a_condition_are_dependencies(self) -> None:
        transport = FakeTransport({FLASK_URL: flask_body()})
        pkg = fetch_pypi_package("Flask", transport=transport)  # type: ignore[arg-type]
        self.assertEqual(transport.requested_urls, [FLASK_URL])  # asked for the canonical name, so no redirect
        self.assertEqual((pkg.name, pkg.slug, pkg.version), ("Flask", "flask", "3.1.3"))
        self.assertEqual(pkg.dependencies, ("blinker", "click", "itsdangerous", "jinja2", "markupsafe", "werkzeug"))
        self.assertEqual(pkg.conditional_dependencies, ("asgiref", "importlib-metadata", "python-dotenv"))
        self.assertEqual(pkg.source_url, FLASK_URL)

    def test_a_package_named_unconditionally_somewhere_is_not_also_conditional(self) -> None:
        body = flask_body(requires_dist=["click>=8", 'click>=8; python_version < "3.9"'])
        pkg = fetch_pypi_package("flask", transport=FakeTransport({FLASK_URL: body}))  # type: ignore[arg-type]
        self.assertEqual((pkg.dependencies, pkg.conditional_dependencies), (("click",), ()))

    def test_a_package_never_depends_on_itself(self) -> None:
        body = flask_body(requires_dist=["Flask[async]>=3"])
        pkg = fetch_pypi_package("flask", transport=FakeTransport({FLASK_URL: body}))  # type: ignore[arg-type]
        self.assertEqual(pkg.dependencies, ())

    def test_no_requirements_is_not_an_error(self) -> None:
        empty: list[str] = []
        for value in (None, empty):
            pkg = fetch_pypi_package(
                "flask",
                transport=FakeTransport({FLASK_URL: flask_body(requires_dist=value)}),  # type: ignore[arg-type]
            )
            self.assertEqual(pkg.dependencies, ())

    def test_not_found(self) -> None:
        with self.assertRaises(PackageNotFoundError):
            fetch_pypi_package("nonexistent-pkg-9999", transport=FakeTransport({}))  # type: ignore[arg-type]

    def test_a_license_that_is_a_whole_licence_text_is_not_kept_as_a_licence_name(self) -> None:
        text = "Permission is hereby granted, free of charge, to any person obtaining a copy\nof this software..."
        plain = FakeTransport({FLASK_URL: flask_body(license=text)})
        self.assertIsNone(fetch_pypi_package("flask", transport=plain).license_spdx)  # type: ignore[arg-type]
        spdx = FakeTransport({FLASK_URL: flask_body(license=text, license_expression="BSD-3-Clause")})
        pkg = fetch_pypi_package("flask", transport=spdx)  # type: ignore[arg-type]
        self.assertEqual(pkg.license_spdx, "BSD-3-Clause")

    def test_answers_of_the_wrong_shape_are_refused_not_crashed_on(self) -> None:
        wrong = [b"[]", b'{"info": []}', b'{"nope": 1}', json.dumps({"info": {"requires_dist": "click"}}).encode(),
                 json.dumps({"info": {"requires_dist": [1, 2]}}).encode(),
                 json.dumps({"info": {"requires_dist": ["x"] * 501}}).encode()]  # fmt: skip
        for body in wrong:
            with self.subTest(body=body[:40]), self.assertRaises(RegistryError):
                fetch_pypi_package("flask", transport=FakeTransport({FLASK_URL: body}))  # type: ignore[arg-type]


class Graph(unittest.TestCase):
    def package(self, **overrides: object) -> PyPIPackage:
        fields: dict[str, object] = dict(
            name="Flask", slug="flask", version="3.0.3", summary="A simple framework", license_spdx="BSD-3-Clause",
            author="Pallets", homepage="https://palletsprojects.com/p/flask", dependencies=("werkzeug", "jinja2"),
            source_url="https://pypi.org/pypi/flask/json",
        )  # fmt: skip
        fields.update(overrides)
        return PyPIPackage(**fields)  # type: ignore[arg-type]

    def test_structure(self) -> None:
        now = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
        graph_data = pypi_package_to_graph(self.package(), retrieved_at=now, kind=EntityKind.FRAMEWORK)
        main, source, ev, deps, rels = graph_data
        self.assertEqual((main.kind, main.slug, main.provenance), (EntityKind.FRAMEWORK, "flask", Provenance.SOURCED))
        self.assertEqual(source.url, "https://pypi.org/pypi/flask/json")
        self.assertEqual(ev.claim, "Flask v3.0.3 on PyPI declares 2 dependencies with no condition")
        self.assertEqual(ev.locator, "info.requires_dist")
        self.assertEqual((len(deps), len(rels)), (2, 2))
        for rel in rels:
            self.assertEqual(rel.source_id, main.id)
            self.assertEqual((rel.type, rel.provenance), (RelationType.DEPENDS_ON, Provenance.SOURCED))
            self.assertEqual(rel.evidence_ids, (ev.id,))

    def test_the_claim_says_how_many_dependencies_were_left_out(self) -> None:
        pkg = self.package(conditional_dependencies=("asgiref", "importlib-metadata"))
        _, _, ev, _, _ = pypi_package_to_graph(pkg, retrieved_at=datetime(2026, 10, 27, tzinfo=UTC))
        self.assertIn("2 more apply only under a condition", ev.claim)
        self.assertIn("not recorded", ev.claim)

    def test_the_source_is_not_called_documentation(self) -> None:
        _, source, _, _, _ = pypi_package_to_graph(self.package(), retrieved_at=datetime(2026, 10, 27, tzinfo=UTC))
        self.assertEqual(source.source_type.value, "other")

    def test_ingest_service_is_idempotent(self) -> None:
        pkg = self.package(name="requests", slug="requests", dependencies=("urllib3", "certifi"),
                           source_url="https://pypi.org/pypi/requests/json")  # fmt: skip
        graph = InMemoryGraph()
        service = RegistryIngestService(graph)
        first = service.ingest_pypi("requests", pkg=pkg)
        self.assertEqual((first.new_entities_count, first.new_relationships_count), (3, 2))
        self.assertEqual((graph.entity_count, graph.relationship_count), (3, 2))
        again = service.ingest_pypi("requests", pkg=pkg)
        self.assertEqual((again.new_entities_count, again.new_relationships_count), (0, 0))
        self.assertEqual((graph.entity_count, graph.relationship_count), (3, 2))


if __name__ == "__main__":
    unittest.main()
