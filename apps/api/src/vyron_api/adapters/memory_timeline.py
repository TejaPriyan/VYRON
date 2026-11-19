from __future__ import annotations

from collections.abc import Collection, Iterable

from vyron_api.domain.timeline import TimelineEvent
from vyron_api.providers.timeline import TimelinePage


class InMemoryTimeline:
    """Reference timeline store. Events are returned oldest first, ties by id."""

    def __init__(self, events: Iterable[TimelineEvent] = ()) -> None:
        self._events: dict[str, TimelineEvent] = {}
        for event in events:
            self.add(event)

    def add(self, event: TimelineEvent) -> None:
        if event.id in self._events:
            raise ValueError(f"duplicate timeline event id: {event.id}")
        self._events[event.id] = event

    @staticmethod
    def _page(events: Iterable[TimelineEvent], limit: int, offset: int) -> TimelinePage:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset must not be negative")
        ordered = sorted(events, key=lambda e: (e.occurred_on, e.id))
        return TimelinePage(items=ordered[offset : offset + limit], total=len(ordered))

    async def for_entities(
        self, entity_ids: Collection[str], *, include_predictions: bool, limit: int, offset: int
    ) -> TimelinePage:
        return self._page(
            (
                e
                for e in self._events.values()
                if e.entity_id in entity_ids and (include_predictions or not e.is_prediction)
            ),
            limit,
            offset,
        )

    async def between(
        self,
        start_year: int | None,
        end_year: int | None,
        *,
        include_predictions: bool,
        limit: int,
        offset: int,
    ) -> TimelinePage:
        """Events within [start_year, end_year]. Predictions are opt-in and never mixed silently."""
        if start_year is not None and end_year is not None and start_year > end_year:
            raise ValueError("start_year must not be after end_year")
        selected = []
        for event in self._events.values():
            if event.is_prediction and not include_predictions:
                continue
            year = event.occurred_on.year
            if (start_year is None or year >= start_year) and (end_year is None or year <= end_year):
                selected.append(event)
        return self._page(selected, limit, offset)
