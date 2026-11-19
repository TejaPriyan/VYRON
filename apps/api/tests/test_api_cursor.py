import unittest

from vyron_api.api.cursor import (
    MAX_OFFSET,
    CursorCodec,
    InvalidCursorError,
    fingerprint,
)

SECRET = b"s" * 32
OTHER = b"o" * 32


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class CursorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.codec = CursorCodec([SECRET], ttl_seconds=3600, now=self.clock)
        self.filters = {"q": "react", "kind": ["library"]}

    def test_round_trip(self) -> None:
        token = self.codec.encode("search", self.filters, 50)
        self.assertEqual(self.codec.decode(token, "search", self.filters), 50)

    def test_token_is_compact_and_url_safe(self) -> None:
        token = self.codec.encode("search", self.filters, 0)
        self.assertLessEqual(len(token), 200)
        self.assertRegex(token, r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

    def test_filter_key_order_does_not_matter(self) -> None:
        token = self.codec.encode("search", {"a": 1, "b": 2}, 5)
        self.assertEqual(self.codec.decode(token, "search", {"b": 2, "a": 1}), 5)

    def test_different_query_or_operation_is_rejected(self) -> None:
        token = self.codec.encode("search", self.filters, 5)
        with self.assertRaisesRegex(InvalidCursorError, "does not belong"):
            self.codec.decode(token, "search", {"q": "vue", "kind": ["library"]})
        with self.assertRaisesRegex(InvalidCursorError, "does not belong"):
            self.codec.decode(token, "list_entities", self.filters)

    def test_tampering_is_rejected(self) -> None:
        token = self.codec.encode("search", self.filters, 5)
        body, mac = token.split(".")
        forged_body = self.codec.encode("search", self.filters, 99999).split(".")[0]
        for forged in (f"{forged_body}.{mac}", f"{body}.{mac[:-2]}AA", f"{body}x.{mac}", f".{mac}", f"{body}."):
            with self.subTest(forged=forged[:30]), self.assertRaises(InvalidCursorError):
                self.codec.decode(forged, "search", self.filters)

    def test_wrong_secret_is_rejected(self) -> None:
        token = CursorCodec([OTHER], now=self.clock).encode("search", self.filters, 5)
        with self.assertRaisesRegex(InvalidCursorError, "signature"):
            self.codec.decode(token, "search", self.filters)

    def test_key_rotation(self) -> None:
        old_token = CursorCodec([OTHER], now=self.clock).encode("search", self.filters, 7)
        rotated = CursorCodec([SECRET, OTHER], now=self.clock)
        self.assertEqual(rotated.decode(old_token, "search", self.filters), 7)
        new_token = rotated.encode("search", self.filters, 8)  # signed with the first key
        self.assertEqual(self.codec.decode(new_token, "search", self.filters), 8)

    def test_expiry(self) -> None:
        token = self.codec.encode("search", self.filters, 5)
        self.clock.now += 3599
        self.assertEqual(self.codec.decode(token, "search", self.filters), 5)
        self.clock.now += 2
        with self.assertRaisesRegex(InvalidCursorError, "expired"):
            self.codec.decode(token, "search", self.filters)

    def test_garbage_is_rejected_without_other_exceptions(self) -> None:
        for garbage in ("", "x", "a.b", "....", "%%%.%%%", "é.é", "a" * 600, "e30.e30"):
            with self.subTest(garbage=garbage[:20]), self.assertRaises(InvalidCursorError):
                self.codec.decode(garbage, "search", self.filters)

    def test_offset_bounds(self) -> None:
        with self.assertRaises(ValueError):
            self.codec.encode("search", self.filters, -1)
        with self.assertRaises(ValueError):
            self.codec.encode("search", self.filters, MAX_OFFSET + 1)

    def test_constructor_rejects_weak_configuration(self) -> None:
        with self.assertRaises(ValueError):
            CursorCodec([])
        with self.assertRaises(ValueError):
            CursorCodec([b"short"])
        with self.assertRaises(ValueError):
            CursorCodec([SECRET], ttl_seconds=0)

    def test_fingerprint_is_stable(self) -> None:
        self.assertEqual(fingerprint("a", {"x": 1}), fingerprint("a", {"x": 1}))
        self.assertNotEqual(fingerprint("a", {"x": 1}), fingerprint("a", {"x": 2}))


if __name__ == "__main__":
    unittest.main()
