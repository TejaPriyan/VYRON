"""What a registry reader keeps of each declared dependency: its name as the registry has it and its version range.

The requirement strings are written from PEP 508 and from npm's package.json documentation; the flask-like and
express-like lists mirror the shape of the real ones reported for those packages. None of it was captured.
"""

import json
import unittest

from registry_fakes import FakeTransport, npm_payload, pypi_payload
from vyron_api.registries.common import PackageReference, clean_requirement
from vyron_api.registries.errors import RegistryError
from vyron_api.registries.npm import fetch_npm_package, npm_reference
from vyron_api.registries.pypi import PyPIPackage, fetch_pypi_package, parse_reference


class RequirementText(unittest.TestCase):
    def test_plain_version_syntax_is_kept(self) -> None:
        for text in (">=3.1.0", ">=1.0,<2", "~=2.0", "==1.*", "^4.1.0", "1.2.x", ">=1 <2 || >=3", "*", "(>=8.1.3)"):
            with self.subTest(text=text):
                self.assertIsNotNone(clean_requirement(text))

    def test_anything_else_is_dropped_not_cleaned(self) -> None:
        for text in ("", "<script>alert(1)</script>", "a" * 101, ">=1\n2", "https://example.invalid/x.zip", "1.0;rm"):
            with self.subTest(text=text[:20]):
                self.assertIsNone(clean_requirement(text))

    def test_outer_parentheses_of_the_old_style_are_removed(self) -> None:
        self.assertEqual(clean_requirement("(>=8.1.3)"), ">=8.1.3")


class PyPIReferences(unittest.TestCase):
    def test_a_name_with_a_range(self) -> None:
        self.assertEqual(parse_reference("Werkzeug>=3.1.0"), PackageReference("werkzeug", ">=3.1.0", True))

    def test_names_come_out_in_pypis_canonical_form(self) -> None:
        self.assertEqual(parse_reference("typing_extensions>=4").name, "typing-extensions")  # type: ignore[union-attr]
        self.assertEqual(parse_reference("Jinja2").name, "jinja2")  # type: ignore[union-attr]

    def test_extras_are_not_part_of_the_name_or_the_range(self) -> None:
        self.assertEqual(parse_reference("requests[security,socks]>=2.0"), PackageReference("requests", ">=2.0", True))

    def test_the_old_parenthesised_style(self) -> None:
        self.assertEqual(parse_reference("click (>=8.1.3)"), PackageReference("click", ">=8.1.3", True))

    def test_a_marker_is_not_part_of_the_range(self) -> None:
        self.assertEqual(
            parse_reference('importlib-metadata>=3.6.0 ; python_version < "3.10"'),
            PackageReference("importlib-metadata", ">=3.6.0", True),
        )

    def test_no_range_is_none_and_still_readable(self) -> None:
        self.assertEqual(parse_reference("itsdangerous"), PackageReference("itsdangerous", None, True))

    def test_a_direct_reference_is_not_readable_and_its_url_is_not_kept(self) -> None:
        found = parse_reference("fixture-direct @ https://example.invalid/fixture-direct.zip")
        self.assertEqual(found, PackageReference("fixture-direct", None, False))

    def test_nothing_that_is_a_name_gives_none(self) -> None:
        for spec in ("", "   ", "@ https://x", "; python_version < '3.9'"):
            with self.subTest(spec=spec):
                self.assertIsNone(parse_reference(spec))


class PyPIFetch(unittest.TestCase):
    URL = "https://pypi.org/pypi/fixture-web/json"

    def read(self, requires: list[str] | None) -> PyPIPackage:
        body = json.dumps(pypi_payload("fixture-web", "2.1.0", requires)).encode()
        return fetch_pypi_package("fixture-web", transport=FakeTransport({self.URL: body}))  # type: ignore[arg-type]

    def test_references_and_left_out_for_a_flask_shaped_list(self) -> None:
        package = self.read(
            [
                "Werkzeug>=3.1.0",
                "click>=8.1.3",
                "itsdangerous>=2.2.0",
                'importlib-metadata>=3.6.0; python_version < "3.10"',
                'asgiref>=3.2; extra == "async"',
                'python-dotenv; extra == "dotenv"',
            ]
        )
        self.assertEqual([r.name for r in package.references], ["click", "itsdangerous", "werkzeug"])
        self.assertEqual(package.references[2].requirement, ">=3.1.0")
        self.assertEqual(package.left_out, (("conditional", 3),))
        self.assertEqual(package.dependencies, ("click", "itsdangerous", "werkzeug"))  # the existing field is unchanged

    def test_a_package_named_with_and_without_a_marker_is_listed_once_and_not_left_out(self) -> None:
        package = self.read(["tomli>=1", 'tomli>=2; python_version < "3.11"'])
        self.assertEqual([(r.name, r.requirement) for r in package.references], [("tomli", ">=1")])
        self.assertEqual(package.left_out, ())

    def test_a_requirement_with_no_readable_name_is_counted_not_dropped_silently(self) -> None:
        package = self.read(["good>=1", "@ https://x"])
        self.assertEqual(package.left_out, (("unusable", 1),))

    def test_no_requirements_is_empty(self) -> None:
        empties: tuple[list[str] | None, ...] = (None, [])
        for requires in empties:
            with self.subTest(requires=requires):
                package = self.read(requires)
                self.assertEqual((package.references, package.left_out), ((), ()))


class NpmReferences(unittest.TestCase):
    def test_a_range_is_kept_and_the_name_is_exact(self) -> None:
        self.assertEqual(npm_reference("express", "^4.1.0"), PackageReference("express", "^4.1.0", True))
        self.assertEqual(npm_reference("@types/node", "*"), PackageReference("@types/node", "*", True))

    def test_what_points_outside_the_registry_is_not_readable_and_not_kept(self) -> None:
        for text in (
            "npm:string-width@^4.2.0",
            "github:fixture-org/repo",
            "fixture-org/repo",
            "fixture-org/repo#main",
            "git+https://example.invalid/r.git",
            "git://example.invalid/r.git",
            "https://example.invalid/r.tgz",
            "file:../r",
            "link:../r",
            "workspace:*",
            "./local",
            "../local",
            "~/local",
        ):
            with self.subTest(text=text):
                self.assertEqual(npm_reference("fixture-x", text), PackageReference("fixture-x", None, False))

    def test_ranges_that_look_a_little_like_a_path_are_still_ranges(self) -> None:
        for text in ("1.2.x", ">=1.0.0 <2.0.0", "^1.0.0 || ^2.0.0", "latest", "~1.2.3", "1.0.0 - 2.0.0"):
            with self.subTest(text=text):
                self.assertTrue(npm_reference("fixture-x", text).readable)  # type: ignore[union-attr]

    def test_a_name_npm_could_not_have_issued_gives_none(self) -> None:
        for name in ("", "Has Space", "../x", "a" * 215, ".hidden", "_private"):
            with self.subTest(name=name):
                self.assertIsNone(npm_reference(name, "^1"))

    def test_a_requirement_that_is_not_text_has_no_range(self) -> None:
        self.assertEqual(npm_reference("fixture-x", {"a": 1}), PackageReference("fixture-x", None, True))


class NpmFetch(unittest.TestCase):
    URL = "https://registry.npmjs.org/fixture-server"

    def test_references_and_what_is_left_out(self) -> None:
        body = json.dumps(
            npm_payload(
                "fixture-server",
                "4.0.0",
                {"fixture-body": "^1.2.0", "fixture-alias": "npm:fixture-body@^1", "Not Valid": "1"},
                development={"fixture-test": "^1", "fixture-lint": "^2"},
                peer={"fixture-peer": ">=2"},
            )
        ).encode()
        transport = FakeTransport({self.URL: body})
        package = fetch_npm_package("fixture-server", transport=transport)  # type: ignore[arg-type]
        self.assertEqual(
            [(r.name, r.requirement, r.readable) for r in package.references],
            [("fixture-alias", None, False), ("fixture-body", "^1.2.0", True)],
        )
        self.assertEqual(package.left_out, (("development", 2), ("peer", 1), ("unusable", 1)))
        self.assertEqual(package.dependencies, ("fixture-alias", "fixture-body"))

    def test_a_scoped_name_is_asked_for_with_the_slash_encoded(self) -> None:
        transport = FakeTransport({"https://registry.npmjs.org/@fixture-scope%2Ftypes": b"{}"})
        with self.assertRaises(RegistryError):  # an empty document is refused; the URL is what matters here
            fetch_npm_package("@fixture-scope/types", transport=transport)  # type: ignore[arg-type]
        self.assertEqual(transport.requested_urls, ["https://registry.npmjs.org/@fixture-scope%2Ftypes"])


if __name__ == "__main__":
    unittest.main()
