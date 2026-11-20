"""Provenance labels for every fact VYRON stores or shows.

The evidence system never lets an unsupported statement pass as a sourced one:
anything that is not backed by a source must carry one of the other labels.
"""

from __future__ import annotations

from enum import StrEnum

SEED_NOTICE = "Development seed data. Not verified production facts."


class Provenance(StrEnum):
    SOURCED = "sourced"  # backed by at least one recorded source/evidence row
    ESTIMATED = "estimated"  # computed from measured inputs with a documented formula
    INFERRED = "inferred"  # derived by deterministic rules from other data
    PREDICTED = "predicted"  # a scenario or forecast, never a fact
    USER_GENERATED = "user_generated"
    AI_GENERATED = "ai_generated"
    SEED = "seed"  # development seed data, not a production fact

    @property
    def requires_evidence(self) -> bool:
        """Only SOURCED claims must point at evidence; the rest are labelled instead."""
        return self is Provenance.SOURCED
