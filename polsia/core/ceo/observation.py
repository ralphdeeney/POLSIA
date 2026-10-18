"""CompanyObservation: a bounded, typed view of company state (mandate §5).

Deliberately limited per section (no database dumps). Every list carries at
most ``limit`` items ordered by relevance; totals are always included so the
CEO knows when truncation occurred. All data comes from tenant-scoped
repositories — an observation never crosses company boundaries.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from polsia.core.planning.entities import Goal, Kpi
from polsia.core.registry.entities import Agent
from polsia.core.shared.ids import CompanyId, OrganizationId
from polsia.core.tasks.entities import Task


@dataclasses.dataclass(frozen=True)
class GoalRef:
    """Minimal goal reference for embedding in observations."""

    id: str
    title: str
    status: str
    priority: str


@dataclasses.dataclass(frozen=True)
class KpiAttention:
    """A KPI flagged for attention (not achieved / not on track)."""

    kpi: Kpi
    goal: GoalRef | None
    reason: str


@dataclasses.dataclass(frozen=True)
class BlockedTask:
    task: Task
    unmet_dependencies: list[str]
    reason: str


@dataclasses.dataclass(frozen=True)
class FailedTask:
    task: Task
    last_error: str | None
    attempts: int
    can_retry: bool


@dataclasses.dataclass(frozen=True)
class PendingApprovalRef:
    id: str
    action: str
    resource_type: str
    resource_id: str | None


@dataclasses.dataclass(frozen=True)
class CompanyObservation:
    """The OBSERVE output: everything the UNDERSTAND/PRIORITIZE stages need,
    and nothing more."""

    organization_id: OrganizationId
    company_id: CompanyId
    generated_at: dt.datetime
    # goals / kpis
    active_goals: list[Goal]
    goals_total: int
    kpis: list[Kpi]
    # total KPIs observed (uncapped); `kpis` itself is bounded by limits
    kpis_total: int
    kpis_attention: list[KpiAttention]
    # tasks
    tasks_total: int
    ready_tasks: list[Task]
    pending_tasks: list[Task]
    running_tasks: list[Task]
    failed_tasks: list[FailedTask]
    blocked_tasks: list[BlockedTask]
    # capabilities
    selectable_agents: list[Agent]
    # approvals
    pending_approvals: list[PendingApprovalRef]
    # digest for decision records (hash of the observation content)
    digest: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable summary (ids and counts; no full entity dumps)."""
        return {
            "company_id": str(self.company_id),
            "generated_at": self.generated_at.isoformat(),
            "digest": self.digest,
            "goals": {
                "total": self.goals_total,
                "active": [str(g.id) for g in self.active_goals],
            },
            "kpis": {
                "total": self.kpis_total,
                "attention": [
                    {"kpi_id": str(a.kpi.id), "status": a.kpi.status.value, "reason": a.reason}
                    for a in self.kpis_attention
                ],
            },
            "tasks": {
                "total": self.tasks_total,
                "ready": [str(t.id) for t in self.ready_tasks],
                "pending": [str(t.id) for t in self.pending_tasks],
                "running": [str(t.id) for t in self.running_tasks],
                "failed": [
                    {
                        "task_id": str(f.task.id),
                        "attempts": f.attempts,
                        "can_retry": f.can_retry,
                        "error": f.last_error,
                    }
                    for f in self.failed_tasks
                ],
                "blocked": [str(b.task.id) for b in self.blocked_tasks],
            },
            "agents": [str(a.id) for a in self.selectable_agents],
            "pending_approvals": [
                {
                    "id": a.id,
                    "action": a.action,
                    "resource_type": a.resource_type,
                    "resource_id": a.resource_id,
                }
                for a in self.pending_approvals
            ],
        }
