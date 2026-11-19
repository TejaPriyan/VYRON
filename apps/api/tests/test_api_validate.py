import unittest

from vyron_api.api.schemas import COMPONENTS, array, enum_of, integer, nullable, obj, ref, string
from vyron_api.api.validate import UnsupportedSchemaError, validate

C = COMPONENTS


def ok(value, schema) -> bool:
    return validate(value, schema, C) == []


class TypeAndKeywordTest(unittest.TestCase):
    def test_booleans_are_not_integers(self) -> None:
        self.assertFalse(ok(True, integer()))
        self.assertTrue(ok(3, integer()))
        self.assertFalse(ok(3.5, integer()))
        self.assertFalse(ok(True, {"type": "number"}))

    def test_string_constraints(self) -> None:
        schema = string(min_length=2, max_length=4, pattern=r"^[a-z]+$")
        self.assertTrue(ok("abc", schema))
        self.assertFalse(ok("a", schema))
        self.assertFalse(ok("abcde", schema))
        self.assertFalse(ok("AB", schema))

    def test_empty_string_message_is_readable(self) -> None:
        issues = validate("", string(min_length=1), C)
        self.assertEqual(issues[0].message, "must not be empty")

    def test_numeric_bounds(self) -> None:
        schema = integer(1, 10)
        self.assertTrue(ok(1, schema) and ok(10, schema))
        self.assertFalse(ok(0, schema) or ok(11, schema))

    def test_enum_and_const(self) -> None:
        self.assertTrue(ok("a", enum_of(("a", "b"))))
        self.assertFalse(ok("c", enum_of(("a", "b"))))
        self.assertFalse(ok("x", {"type": "string", "const": "y"}))

    def test_nullable(self) -> None:
        self.assertTrue(ok(None, nullable(string())))
        self.assertTrue(ok("x", nullable(string())))
        self.assertFalse(ok(1, nullable(string())))
        self.assertTrue(ok(None, nullable(ref("Entity"))))

    def test_arrays(self) -> None:
        schema = array(integer(), max_items=2, min_items=1)
        self.assertTrue(ok([1], schema))
        self.assertFalse(ok([], schema))
        self.assertFalse(ok([1, 2, 3], schema))
        self.assertFalse(ok([1, "x"], schema))
        self.assertFalse(ok([1, 1], {"type": "array", "uniqueItems": True}))

    def test_closed_objects_reject_extra_and_missing_properties(self) -> None:
        schema = obj({"a": integer(), "b": string()}, optional=("b",))
        self.assertTrue(ok({"a": 1}, schema))
        self.assertFalse(ok({}, schema))
        self.assertFalse(ok({"a": 1, "z": 2}, schema))

    def test_additional_properties_schema(self) -> None:
        schema = {"type": "object", "additionalProperties": integer(0)}
        self.assertTrue(ok({"x": 1, "y": 2}, schema))
        self.assertFalse(ok({"x": -1}, schema))

    def test_max_properties_bounds_an_open_object(self) -> None:
        schema = {"type": "object", "additionalProperties": integer(0), "maxProperties": 2}
        self.assertTrue(ok({}, schema))
        self.assertTrue(ok({"a": 1, "b": 2}, schema))
        self.assertFalse(ok({"a": 1, "b": 2, "c": 3}, schema))

    def test_any_of_and_one_of(self) -> None:
        self.assertTrue(ok(1, {"anyOf": [integer(), string()]}))
        self.assertFalse(ok(1.5, {"anyOf": [integer(), string()]}))
        self.assertFalse(ok(1, {"oneOf": [integer(), integer(0)]}))  # matches both
        self.assertTrue(ok(-1, {"oneOf": [integer(), integer(0)]}))

    def test_paths_name_the_failing_field(self) -> None:
        issues = validate({"items": [{"id": "x"}]}, obj({"items": array(obj({"id": integer()}))}), C)
        self.assertEqual(issues[0].path, "$.items[0].id")


class FormatTest(unittest.TestCase):
    def test_formats(self) -> None:
        cases = [
            ("uuid", "65f9394b-42af-5055-a3a8-72312e13e423", True),
            ("uuid", "not-a-uuid", False),
            ("uuid", "65F9394B-42AF-5055-A3A8-72312E13E423", True),
            ("date", "2026-10-08", True),
            ("date", "2026-13-01", False),
            ("date", "20261008", False),
            ("date-time", "2026-10-08T10:00:00+00:00", True),
            ("date-time", "2026-10-08T10:00:00Z", True),
            ("date-time", "2026-10-08T10:00:00", False),  # no timezone
            ("uri", "https://github.com/a/b", True),
            ("uri", "github.com/a/b", False),
        ]
        for name, value, expected in cases:
            with self.subTest(name=name, value=value):
                self.assertEqual(ok(value, {"type": "string", "format": name}), expected)


class RefAndStrictnessTest(unittest.TestCase):
    def test_refs_resolve(self) -> None:
        self.assertTrue(ok("language", ref("EntityKind")))
        self.assertFalse(ok("spaceship", ref("EntityKind")))

    def test_unresolved_or_foreign_refs_raise(self) -> None:
        with self.assertRaises(UnsupportedSchemaError):
            validate(1, {"$ref": "#/components/schemas/Nope"}, C)
        with self.assertRaises(UnsupportedSchemaError):
            validate(1, {"$ref": "http://elsewhere/schema"}, C)

    def test_unknown_keywords_fail_loudly(self) -> None:
        with self.assertRaises(UnsupportedSchemaError):
            validate(1, {"type": "integer", "multipleOf": 2}, C)
        with self.assertRaises(UnsupportedSchemaError):
            validate("x", {"type": "string", "format": "hostname"}, C)

    def test_cyclic_refs_do_not_recurse_forever(self) -> None:
        cyclic = {"A": {"$ref": "#/components/schemas/A"}}
        with self.assertRaises(UnsupportedSchemaError):
            validate(1, cyclic["A"], cyclic)


if __name__ == "__main__":
    unittest.main()
