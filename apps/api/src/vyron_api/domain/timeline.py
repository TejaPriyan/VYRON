"""Timeline events and the rule that the future is never presented as history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from vyron_api.domain.enums import DatePrecision
from vyron_api.domain.provenance import Provenance


class FutureEventNotLabelledError(ValueError):
    """A future-dated event was not marked as a prediction."""


def is_future(occurred_on: date, precision: DatePrecision, today: date) -> bool:
    """True if the event lies strictly after the period containing ``today``."""
    if precision is DatePrecision.DAY:
        return occurred_on > today
    if precision is DatePrecision.MONTH:
        return (occurred_on.year, occurred_on.month) > (today.year, today.month)
    if precision is DatePrecision.YEAR:
        return occurred_on.year > today.year
    return occurred_on.year // 10 * 10 > today.year


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    id: str
    entity_id: str
    occurred_on: date
    precision: DatePrecision
    title: str
    event_type: str
    provenance: Provenance
    is_prediction: bool = False
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.event_type.strip():
            raise ValueError("timeline event title and type must not be empty")
        if self.is_prediction != (self.provenance is Provenance.PREDICTED):
            raise ValueError("is_prediction must be true exactly when provenance is 'predicted'")

    def check_labelled_for(self, today: date) -> None:
        if is_future(self.occurred_on, self.precision, today) and not self.is_prediction:
            raise FutureEventNotLabelledError(
                f"event {self.id!r} is dated after {today.isoformat()} but is not a prediction"
            )
