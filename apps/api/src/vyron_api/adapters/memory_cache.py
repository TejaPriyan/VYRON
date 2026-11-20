from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable


class InMemoryTTLCache:
    """Process-local TTL + LRU cache for tests and as the fallback when Redis is down."""

    def __init__(self, max_entries: int = 1024, clock: Callable[[], float] = time.monotonic) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._clock = clock
        self._data: OrderedDict[str, tuple[float, bytes]] = OrderedDict()

    @property
    def size(self) -> int:
        return len(self._data)

    async def get(self, key: str) -> bytes | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= self._clock():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    async def set(self, key: str, value: bytes, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._data[key] = (self._clock() + ttl_seconds, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)
