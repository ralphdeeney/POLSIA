"""Value objects and the deterministic KPI evaluator for the planning domain."""

from __future__ import annotations

import enum


class GoalStatus(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


#: Allowed goal transitions. COMPLETED and CANCELLED are terminal.
GOAL_STATUS_TRANSITIONS: dict[GoalStatus, frozenset[GoalStatus]] = {
    GoalStatus.DRAFT: frozenset({GoalStatus.ACTIVE, GoalStatus.CANCELLED}),
    GoalStatus.ACTIVE: frozenset({GoalStatus.PAUSED, GoalStatus.COMPLETED, GoalStatus.CANCELLED}),
    GoalStatus.PAUSED: frozenset({GoalStatus.ACTIVE, GoalStatus.CANCELLED}),
    GoalStatus.COMPLETED: frozenset(),
    GoalStatus.CANCELLED: frozenset(),
}


class Priority(enum.StrEnum):
    """Generic work priority shared by goals and tasks."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class KpiDirection(enum.StrEnum):
    """Whether the metric should go up, down, or stay at the target."""

    INCREASE = "increase"
    DECREASE = "decrease"
    MAINTAIN = "maintain"


class KpiStatus(enum.StrEnum):
    NOT_STARTED = "not_started"
    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    ACHIEVED = "achieved"
    MISSED = "missed"


def evaluate_kpi_status(
    *,
    direction: KpiDirection,
    current: float | None,
    target: float,
    deadline_passed: bool = False,
    tolerance: float = 0.1,
    risk_band: float = 0.8,
) -> KpiStatus:
    """Deterministically evaluate a KPI's status. Pure function — no AI, no
    business semantics.

    Rules:
    - ``current is None`` → NOT_STARTED (nothing measured yet).
    - Achieved: INCREASE → current >= target; DECREASE → current <= target;
      MAINTAIN → within ``tolerance`` (relative) of target.
    - MISSED: not achieved AND ``deadline_passed``.
    - Otherwise ON_TRACK when progress is at or beyond the ``risk_band``
      fraction of the target (INCREASE/DECREASE) or within ``2 * tolerance``
      (MAINTAIN); AT_RISk otherwise.
    """
    if not 0 < risk_band <= 1:
        raise ValueError("risk_band must be in (0, 1]")
    if tolerance <= 0:
        raise ValueError("tolerance must be positive")
    if current is None:
        return KpiStatus.NOT_STARTED

    cur = current

    def _achieved() -> bool:
        if direction is KpiDirection.INCREASE:
            return cur >= target
        if direction is KpiDirection.DECREASE:
            return cur <= target
        # MAINTAIN
        reference = max(abs(target), 1e-12)
        return abs(cur - target) / reference <= tolerance

    if _achieved():
        return KpiStatus.ACHIEVED
    if deadline_passed:
        return KpiStatus.MISSED

    if direction is KpiDirection.INCREASE:
        reference = max(abs(target), 1e-12)
        return KpiStatus.ON_TRACK if cur / reference >= risk_band else KpiStatus.AT_RISK
    if direction is KpiDirection.DECREASE:
        if cur <= 0:
            return KpiStatus.ON_TRACK
        return KpiStatus.ON_TRACK if target / cur >= risk_band else KpiStatus.AT_RISK
    # MAINTAIN
    reference = max(abs(target), 1e-12)
    deviation = abs(cur - target) / reference
    if deviation <= tolerance * 2:
        return KpiStatus.ON_TRACK
    return KpiStatus.AT_RISK
