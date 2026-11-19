from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from vyron_api.domain.timeline import TimelineEvent


@dataclass(frozen=True, slots=True)
class TimelinePage:
    items: Sequence[TimelineEvent]
    total: int


@runtime_checkable
class TimelineRepository(Protocol):
    """Events come back oldest first, ties broken by id, so offset paging is stable.

    Both methods are paged so no endpoint can return an unbounded number of events.
    """

    async def for_entities(
        self, entity_ids: Collection[str], *, include_predictions: bool, limit: int, offset: int
    ) -> TimelinePage: ...

    async def between(
        self,
        start_year: int | None,
        end_year: int | None,
        *,
        include_predictions: bool,
        limit: int,
        offset: int,
    ) -> TimelinePage:
        """Events within [start_year, end_year]; predictions only when asked for."""
