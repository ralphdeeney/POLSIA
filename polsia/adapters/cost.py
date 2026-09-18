"""Null cost calculator adapter (Phase 2): never prices anything.

Provider-neutral: a real adapter maps model references to pricing tables
without changing the domain.
"""

from __future__ import annotations

from decimal import Decimal

from polsia.core.cost.ports import UsageRecord


class NullCostCalculator:
    """CostCalculator port that returns None for everything (no pricing)."""

    def estimate(self, record: UsageRecord) -> Decimal | None:  # noqa: ARG002
        return None
