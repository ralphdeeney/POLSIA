"""Scheduling and orchestration ports (Phase 2 — contracts only).

SchedulerPort expresses *when* work should run (immediate, at a timestamp,
periodically, after an event, when a condition holds). OrchestratorPort
expresses goal -> task decomposition. Phase 2 ships deterministic stubs; no
Celery/Redis/Temporal, and no autonomous reasoning.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
from typing import Any, Protocol

from polsia.core.shared.ids import CompanyId, GoalId


class ScheduleKind(enum.StrEnum):
    IMMEDIATE = "immediate"
    AT = "at"
    PERIODIC = "periodic"
    AFTER_EVENT = "after_event"
    CONDITION = "condition"


@dataclasses.dataclass(frozen=True)
class ScheduleSpec:
    """A provider-neutral scheduling request."""

    kind: ScheduleKind
    at: dt.datetime | None = None
    interval_seconds: int | None = None
    event_type: str | None = None
    condition: str | None = None  # free-form expression owned by the caller
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class ScheduledHandle:
    """Opaque handle returned when a schedule is registered."""

    id: str
    kind: ScheduleKind
    next_run_at: dt.datetime | None = None


class SchedulerPort(Protocol):
    """Port for scheduling. A production implementation (cron/Celery/Temporal/
    custom worker) can replace the stub without touching the domain."""

    async def schedule(self, spec: ScheduleSpec, *, company_id: CompanyId) -> ScheduledHandle: ...

    async def cancel(self, handle_id: str) -> None: ...


# -- Orchestration --------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class TaskDraft:
    """A proposed task produced by decomposition. The caller (orchestration
    service) persists it as a real Task."""

    title: str
    description: str | None = None
    depends_on_indexes: list[int] = dataclasses.field(default_factory=list)
    assigned_skill: str | None = None
    priority: str | None = None
    input: dict[str, Any] = dataclasses.field(default_factory=dict)
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class DecompositionPlan:
    """Ordered task drafts plus the rationale for the decomposition."""

    tasks: list[TaskDraft]
    rationale: str | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


class OrchestratorPort(Protocol):
    """Port for turning a goal into tasks.

    Phase 2 ships a deterministic stub (single task per goal, or one task per
    provided objective). Autonomous planning/reasoning is a later phase.
    """

    async def decompose(
        self,
        *,
        company_id: CompanyId,
        goal_id: GoalId,
        goal_title: str,
        goal_description: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> DecompositionPlan: ...


__all__ = [
    "DecompositionPlan",
    "OrchestratorPort",
    "ScheduleKind",
    "ScheduleSpec",
    "ScheduledHandle",
    "SchedulerPort",
    "TaskDraft",
]
