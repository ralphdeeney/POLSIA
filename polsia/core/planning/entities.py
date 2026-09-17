"""Goal and KPI domain entities (company-scoped, frozen dataclasses)."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from polsia.core.planning.values import (
    GOAL_STATUS_TRANSITIONS,
    GoalStatus,
    KpiDirection,
    KpiStatus,
    Priority,
    evaluate_kpi_status,
)
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import utcnow
from polsia.core.shared.ids import CompanyId, GoalId, KpiId


def _require_title(title: str) -> str:
    if not title.strip():
        raise ValidationError("title must not be empty")
    return title.strip()


@dataclasses.dataclass(frozen=True)
class Goal:
    """A company-scoped business intent. The OS never interprets its meaning."""

    id: GoalId
    company_id: CompanyId
    title: str
    description: str | None = None
    parent_goal_id: GoalId | None = None
    status: GoalStatus = GoalStatus.DRAFT
    priority: Priority = Priority.MEDIUM
    due_at: dt.datetime | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        company_id: CompanyId,
        title: str,
        description: str | None = None,
        parent_goal_id: GoalId | None = None,
        priority: Priority = Priority.MEDIUM,
        due_at: dt.datetime | None = None,
        metadata: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> Goal:
        ts = now or utcnow()
        return Goal(
            id=GoalId.generate(),
            company_id=company_id,
            title=_require_title(title),
            description=description,
            parent_goal_id=parent_goal_id,
            status=GoalStatus.DRAFT,
            priority=priority,
            due_at=due_at,
            metadata=dict(metadata or {}),
            created_at=ts,
            updated_at=ts,
        )

    def _mutated(self, **changes: Any) -> Goal:
        return dataclasses.replace(self, updated_at=utcnow(), **changes)

    def rename(self, title: str) -> Goal:
        return self._mutated(title=_require_title(title))

    def update(
        self,
        *,
        description: str | None = None,
        priority: Priority | None = None,
        due_at: dt.datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Goal:
        changes: dict[str, Any] = {}
        if description is not None:
            changes["description"] = description
        if priority is not None:
            changes["priority"] = priority
        if due_at is not None:
            changes["due_at"] = due_at
        if metadata is not None:
            changes["metadata"] = dict(metadata)
        return self._mutated(**changes)

    def transition_status(self, new_status: GoalStatus) -> Goal:
        if new_status not in GOAL_STATUS_TRANSITIONS[self.status]:
            allowed = ", ".join(sorted(s.value for s in GOAL_STATUS_TRANSITIONS[self.status]))
            raise ValidationError(
                f"goal {self.status.value!r} cannot transition to {new_status.value!r} "
                f"(allowed: {allowed or 'none — terminal'})"
            )
        return self._mutated(status=new_status)


@dataclasses.dataclass(frozen=True)
class Kpi:
    """A generic measurement attached to a company (optionally to a goal).

    ``current_value``/``target_value`` are unitless numbers; ``unit`` is a
    free-form label owned by the company. Status is derived deterministically
    by :func:`evaluate_kpi_status` — never by AI.
    """

    id: KpiId
    company_id: CompanyId
    name: str
    direction: KpiDirection
    target_value: float
    goal_id: GoalId | None = None
    description: str | None = None
    unit: str | None = None
    current_value: float | None = None
    status: KpiStatus = KpiStatus.NOT_STARTED
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        company_id: CompanyId,
        name: str,
        direction: KpiDirection,
        target_value: float,
        goal_id: GoalId | None = None,
        description: str | None = None,
        unit: str | None = None,
        metadata: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> Kpi:
        ts = now or utcnow()
        return Kpi(
            id=KpiId.generate(),
            company_id=company_id,
            name=_require_title(name),
            direction=direction,
            target_value=target_value,
            goal_id=goal_id,
            description=description,
            unit=unit,
            current_value=None,
            status=KpiStatus.NOT_STARTED,
            metadata=dict(metadata or {}),
            created_at=ts,
            updated_at=ts,
        )

    def _mutated(self, **changes: Any) -> Kpi:
        return dataclasses.replace(self, updated_at=utcnow(), **changes)

    def update(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        target_value: float | None = None,
        direction: KpiDirection | None = None,
        unit: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Kpi:
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = _require_title(name)
        if description is not None:
            changes["description"] = description
        if target_value is not None:
            changes["target_value"] = target_value
        if direction is not None:
            changes["direction"] = direction
        if unit is not None:
            changes["unit"] = unit
        if metadata is not None:
            changes["metadata"] = dict(metadata)
        return self._mutated(**changes)

    def record_measurement(
        self,
        current_value: float,
        *,
        deadline_passed: bool = False,
        now: dt.datetime | None = None,
    ) -> Kpi:
        """Record a new measurement and re-evaluate status deterministically.

        ``deadline_passed`` is supplied by the service layer (e.g. derived
        from the owning goal's due date); the entity stays pure.
        """
        _ = now
        status = evaluate_kpi_status(
            direction=self.direction,
            current=current_value,
            target=self.target_value,
            deadline_passed=deadline_passed,
        )
        return self._mutated(current_value=current_value, status=status)
