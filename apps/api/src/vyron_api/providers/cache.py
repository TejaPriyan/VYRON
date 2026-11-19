from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CacheProvider(Protocol):
    """Byte-oriented cache. Serialization belongs to the caller, so backends stay swappable."""

    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, ttl_seconds: float) -> None: ...

    async def delete(self, key: str) -> None: ...
