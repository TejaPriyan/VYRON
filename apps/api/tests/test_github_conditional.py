import base64
import json
import unittest
from collections.abc import Mapping

from vyron_api.adapters.memory_cache import InMemoryTTLCache
from vyron_api.github.conditional import (
    MAX_CACHED_BODY_BYTES,
    CacheEntry,
    cache_key,
    cached_get,
    conditional_headers,
    decode_entry,
    encode_entry,
)
from vyron_api.github.retry import RawResponse
from vyron_api.providers.github import (
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)

NOW = 1_000_000.0
BODY = b'{"stars": 1}'


class Script:
    """Sender double that records headers and plays back responses or exceptions."""

    def __init__(self, *items: RawResponse | Exception) -> None:
        self.items = list(items)
        self.calls: list[Mapping[str, str]] = []

    async def __call__(self, headers: Mapping[str, str]) -> RawResponse:
        self.calls.append(dict(headers))
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, Exception):
            raise item
        return item


async def no_sleep(_: float) -> None:
    return None


class EntryCodecTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        entry = CacheEntry('"abc"', "Wed, 21 Oct 2026 07:28:00 GMT", BODY, NOW)
        self.assertEqual(decode_entry(encode_entry(entry)), entry)

    def test_corrupt_entries_are_misses(self) -> None:
        good = json.loads(encode_entry(CacheEntry('"a"', None, BODY, NOW)))
        corrupt: list[bytes | None] = [
            None,
            b"",
            b"not json",
            b"[]",
            json.dumps({**good, "v": 99}).encode(),
            json.dumps({**good, "body": "***not base64***"}).encode(),
            json.dumps({**good, "body": 5}).encode(),
            json.dumps({**good, "etag": 5}).encode(),
            json.dumps({**good, "last_modified": []}).encode(),
            json.dumps({**good, "stored_at": "now"}).encode(),
            json.dumps({**good, "stored_at": True}).encode(),
            json.dumps({k: v for k, v in good.items() if k != "body"}).encode(),
        ]
        for data in corrupt:
            with self.subTest(data=(data or b"")[:40]):
                self.assertIsNone(decode_entry(data))

    def test_conditional_headers(self) -> None:
        self.assertEqual(conditional_headers(None), {})
        self.assertEqual(
            conditional_headers(CacheEntry('"a"', "LM", b"", NOW)),
            {"if-none-match": '"a"', "if-modified-since": "LM"},
        )
        self.assertEqual(conditional_headers(CacheEntry(None, None, b"", NOW)), {})

    def test_cache_key_is_stable_and_bounded(self) -> None:
        self.assertEqual(cache_key("/repos/a/b"), cache_key("/repos/a/b"))
        self.assertNotEqual(cache_key("/repos/a/b"), cache_key("/repos/a/c"))
        self.assertLess(len(cache_key("/x" * 5000)), 100)


class CachedGetTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cache = InMemoryTTLCache()
        self.key = cache_key("/repos/a/b")

    async def get(self, script: Script, **kwargs: object) -> object:
        return await cached_get(
            self.cache,
            self.key,
            script,
            now=lambda: NOW,
            sleep=no_sleep,
            **kwargs,  # type: ignore[arg-type]
        )

    async def test_miss_then_revalidate_with_304(self) -> None:
        first = Script(RawResponse(200, {"etag": '"v1"'}, BODY))
        result = await self.get(first)
        self.assertEqual((result.from_cache, result.stale), (False, False))  # type: ignore[attr-defined]
        self.assertEqual(first.calls, [{}])

        second = Script(RawResponse(304, {"x-ratelimit-remaining": "4999"}, b""))
        result = await self.get(second)
        self.assertEqual(second.calls, [{"if-none-match": '"v1"'}])
        self.assertTrue(result.from_cache)  # type: ignore[attr-defined]
        self.assertEqual(result.response.body, BODY)  # type: ignore[attr-defined]
        self.assertEqual(result.response.status, 200)  # type: ignore[attr-defined]
        self.assertEqual(result.response.headers["x-ratelimit-remaining"], "4999")  # type: ignore[attr-defined]

    async def test_changed_resource_replaces_the_entry(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        result = await self.get(Script(RawResponse(200, {"etag": '"v2"'}, b'{"stars": 2}')))
        self.assertFalse(result.from_cache)  # type: ignore[attr-defined]
        entry = decode_entry(await self.cache.get(self.key))
        assert entry is not None
        self.assertEqual((entry.etag, entry.body), ('"v2"', b'{"stars": 2}'))

    async def test_fresh_entries_skip_the_network(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        never = Script(GitHubError("must not be called"))
        result = await self.get(never, fresh_for_seconds=60.0)
        self.assertEqual(never.calls, [])
        self.assertTrue(result.from_cache)  # type: ignore[attr-defined]
        self.assertFalse(result.stale)  # type: ignore[attr-defined]

    async def test_responses_without_validators_or_too_large_are_not_cached(self) -> None:
        await self.get(Script(RawResponse(200, {}, BODY)))
        self.assertIsNone(await self.cache.get(self.key))
        huge = b"x" * (MAX_CACHED_BODY_BYTES + 1)
        await self.get(Script(RawResponse(200, {"etag": '"big"'}, huge)))
        self.assertIsNone(await self.cache.get(self.key))

    async def test_corrupt_entry_is_treated_as_a_miss(self) -> None:
        await self.cache.set(self.key, b"garbage", 60)
        script = Script(RawResponse(200, {"etag": '"v1"'}, BODY))
        result = await self.get(script)
        self.assertEqual(script.calls, [{}])
        self.assertFalse(result.from_cache)  # type: ignore[attr-defined]

    async def test_stale_entry_served_when_github_is_down(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        result = await self.get(Script(OSError("down")), max_retries=1)
        self.assertTrue(result.stale and result.from_cache)  # type: ignore[attr-defined]
        self.assertEqual(result.response.body, BODY)  # type: ignore[attr-defined]

    async def test_stale_entry_served_when_rate_limited(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        limited = Script(RawResponse(429, {"retry-after": "9999"}, b""))
        result = await self.get(limited)
        self.assertTrue(result.stale)  # type: ignore[attr-defined]

    async def test_errors_propagate_without_an_entry_or_when_disabled(self) -> None:
        with self.assertRaises(GitHubUnavailableError):
            await self.get(Script(OSError("down")), max_retries=1)
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        with self.assertRaises(GitHubRateLimitedError):
            await self.get(Script(RawResponse(429, {"retry-after": "9999"}, b"")), serve_stale_on_error=False)

    async def test_not_found_is_never_served_stale(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        with self.assertRaises(GitHubNotFoundError):
            await self.get(Script(RawResponse(404, {}, b"{}")))

    async def test_304_without_validators_is_an_error(self) -> None:
        with self.assertRaises(GitHubError):
            await self.get(Script(RawResponse(304, {}, b"")))

    async def test_last_modified_only_validators(self) -> None:
        await self.get(Script(RawResponse(200, {"last-modified": "LM"}, BODY)))
        script = Script(RawResponse(304, {}, b""))
        await self.get(script)
        self.assertEqual(script.calls, [{"if-modified-since": "LM"}])

    def test_codec_uses_plain_base64(self) -> None:
        raw = json.loads(encode_entry(CacheEntry(None, None, BODY, NOW)))
        self.assertEqual(base64.b64decode(raw["body"]), BODY)


class AsOfTest(unittest.IsolatedAsyncioTestCase):
    """``as_of`` is when GitHub last said this was current; the flags say how the result was obtained."""

    def setUp(self) -> None:
        self.cache = InMemoryTTLCache()
        self.key = cache_key("/repos/a/b")
        self.now = NOW

    async def get(self, script: Script, **kwargs: object):  # type: ignore[no-untyped-def]
        return await cached_get(
            self.cache,
            self.key,
            script,
            now=lambda: self.now,
            sleep=no_sleep,
            **kwargs,  # type: ignore[arg-type]
        )

    async def test_a_read_just_made_is_as_of_now_and_neither_stale_nor_revalidated(self) -> None:
        result = await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        flags = (result.as_of, result.from_cache, result.stale, result.revalidated)
        self.assertEqual(flags, (NOW, False, False, False))

    async def test_a_304_is_as_of_the_moment_it_was_confirmed_and_is_revalidated(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        self.now = NOW + 500
        result = await self.get(Script(RawResponse(304, {}, b"")))
        flags = (result.as_of, result.from_cache, result.stale, result.revalidated)
        self.assertEqual(flags, (NOW + 500, True, False, True))

    async def test_a_hit_inside_the_fresh_window_keeps_the_time_it_was_last_confirmed(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        self.now = NOW + 30
        result = await self.get(Script(GitHubError("must not be called")), fresh_for_seconds=60.0)
        flags = (result.as_of, result.from_cache, result.stale, result.revalidated)
        self.assertEqual(flags, (NOW, True, False, False))

    async def test_the_fresh_window_is_measured_from_the_last_confirmation_not_the_first_read(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        self.now = NOW + 100
        await self.get(Script(RawResponse(304, {}, b"")), fresh_for_seconds=60.0)  # confirmed again at +100
        self.now = NOW + 130
        never = Script(GitHubError("must not be called"))
        result = await self.get(never, fresh_for_seconds=60.0)  # 30 s after the confirmation: still fresh
        self.assertEqual((never.calls, result.as_of), ([], NOW + 100))

    async def test_an_entry_just_outside_the_window_is_asked_about_again(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        self.now = NOW + 61
        script = Script(RawResponse(304, {}, b""))
        result = await self.get(script, fresh_for_seconds=60.0)
        self.assertEqual(len(script.calls), 1)
        self.assertTrue(result.revalidated)

    async def test_a_stale_copy_is_as_of_when_it_was_last_confirmed(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        self.now = NOW + 900
        result = await self.get(Script(OSError("down")), max_retries=1)
        flags = (result.as_of, result.from_cache, result.stale, result.revalidated)
        self.assertEqual(flags, (NOW, True, True, False))

    async def test_a_changed_resource_is_as_of_now(self) -> None:
        await self.get(Script(RawResponse(200, {"etag": '"v1"'}, BODY)))
        self.now = NOW + 500
        result = await self.get(Script(RawResponse(200, {"etag": '"v2"'}, b'{"stars": 2}')))
        self.assertEqual((result.as_of, result.from_cache, result.revalidated), (NOW + 500, False, False))


if __name__ == "__main__":
    unittest.main()
