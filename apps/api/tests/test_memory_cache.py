import unittest

from vyron_api.adapters.memory_cache import InMemoryTTLCache
from vyron_api.providers.cache import CacheProvider


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class InMemoryTTLCacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.cache = InMemoryTTLCache(max_entries=2, clock=self.clock)

    async def test_set_and_get(self) -> None:
        await self.cache.set("a", b"1", ttl_seconds=10)
        self.assertEqual(await self.cache.get("a"), b"1")

    async def test_missing_key_returns_none(self) -> None:
        self.assertIsNone(await self.cache.get("nope"))

    async def test_entries_expire(self) -> None:
        await self.cache.set("a", b"1", ttl_seconds=10)
        self.clock.now = 9.9
        self.assertEqual(await self.cache.get("a"), b"1")
        self.clock.now = 10.0
        self.assertIsNone(await self.cache.get("a"))
        self.assertEqual(self.cache.size, 0)

    async def test_least_recently_used_entry_is_evicted(self) -> None:
        await self.cache.set("a", b"1", ttl_seconds=10)
        await self.cache.set("b", b"2", ttl_seconds=10)
        await self.cache.get("a")  # refresh "a" so "b" is now the oldest
        await self.cache.set("c", b"3", ttl_seconds=10)
        self.assertIsNone(await self.cache.get("b"))
        self.assertEqual(await self.cache.get("a"), b"1")
        self.assertEqual(await self.cache.get("c"), b"3")

    async def test_overwrite_resets_ttl(self) -> None:
        await self.cache.set("a", b"1", ttl_seconds=10)
        self.clock.now = 8.0
        await self.cache.set("a", b"2", ttl_seconds=10)
        self.clock.now = 15.0
        self.assertEqual(await self.cache.get("a"), b"2")

    async def test_delete_is_idempotent(self) -> None:
        await self.cache.set("a", b"1", ttl_seconds=10)
        await self.cache.delete("a")
        await self.cache.delete("a")
        self.assertIsNone(await self.cache.get("a"))

    async def test_non_positive_ttl_is_rejected(self) -> None:
        for ttl in (0, -1):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                await self.cache.set("a", b"1", ttl_seconds=ttl)

    def test_invalid_capacity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            InMemoryTTLCache(max_entries=0)

    def test_satisfies_cache_provider_protocol(self) -> None:
        self.assertIsInstance(self.cache, CacheProvider)


if __name__ == "__main__":
    unittest.main()
