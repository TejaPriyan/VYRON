from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    """A source a provider actually returned. URLs are never synthesized by VYRON."""

    url: str
    title: str
    publisher: str | None
    published_at: datetime | None
    retrieved_at: datetime


class ResearchProvider(Protocol):
    async def search(self, query: str, limit: int) -> Sequence[SourceCandidate]: ...
