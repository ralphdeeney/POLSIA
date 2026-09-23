"""CompanyStateObserver (Phase 3, mandate §5): builds the bounded OBSERVE view.

Every section is fetched from the tenant-scoped Phase 2 repositories and
capped; totals always accompany capped lists so the CEO can detect
truncation. The digest is a stable hash of the observation content — it is
recorded in decision records so any CEO decision can be traced back to the
exact state it was made from.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json

from polsia.core.ceo.observation import (
    BlockedTask,
    CompanyObservation,
    FailedTask,
    GoalRef,
    KpiAttention,
    PendingApprovalRef,
)
from polsia.core.permissions.ports import ApprovalRepository
from polsia.core.planning.ports import GoalRepository, KpiRepository
from polsia.core.planning.values import GoalStatus, KpiStatus
from polsia.core.registry.ports import AgentRepository
from polsia.core.shared.events import utcnow
from polsia.core.shared.ids import CompanyId, OrganizationId, TaskId
from polsia.core.tasks.entities import Task
from polsia.core.tasks.ports import TaskDependencyRepository, TaskRepository
from polsia.core.tasks.values import TaskStatus


@dataclasses.dataclass(frozen=True)
class ObservationLimits:
    """Caps per section (mandate §5: avoid unnecessary data / no DB dumps)."""

    active_goals: int = 50
    kpis: int = 100
    ready_tasks: int = 50
    pending_tasks: int = 50
    running_tasks: int = 50
    failed_tasks: int = 50
    blocked_tasks: int = 50
    agents: int = 100
    pending_approvals: int = 50


_DEFAULT_LIMITS = ObservationLimits()

#: KPI statuses that require CEO attention (achieved is fine; not_started has
#: no signal yet; missed/at_risk are the actionable bands).
_ATTENTION_STATUSES = frozenset({KpiStatus.AT_RISK, KpiStatus.MISSED})


def _digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class CompanyStateObserver:
    """Builds a :class:`CompanyObservation` for one company (tenant-scoped)."""

    def __init__(
        self,
        goals: GoalRepository,
        kpis: KpiRepository,
        tasks: TaskRepository,
        dependencies: TaskDependencyRepository,
        agents: AgentRepository,
        approvals: ApprovalRepository,
        limits: ObservationLimits | None = None,
        clock: type[dt.datetime] | None = None,
    ) -> None:
        self._goals = goals
        self._kpis = kpis
        self._tasks = tasks
        self._dependencies = dependencies
        self._agents = agents
        self._approvals = approvals
        self._limits = limits or _DEFAULT_LIMITS

    async def observe(
        self,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        now: dt.datetime | None = None,
    ) -> CompanyObservation:
        """Fetch, classify, cap, and digest the company's current state."""
        limits = self._limits
        generated_at = now or utcnow()

        all_goals = await self._goals.list_for_company(company_id)
        active_goals = [
            g
            for g in all_goals
            if g.status in (GoalStatus.ACTIVE, GoalStatus.DRAFT, GoalStatus.PAUSED)
        ]
        goals_by_id = {g.id: g for g in all_goals}

        all_kpis = await self._kpis.list_for_company(company_id)
        attention: list[KpiAttention] = []
        for kpi in all_kpis:
            if kpi.status not in _ATTENTION_STATUSES:
                continue
            owner = goals_by_id.get(kpi.goal_id) if kpi.goal_id else None
            goal_ref = (
                GoalRef(
                    id=str(owner.id),
                    title=owner.title,
                    status=owner.status.value,
                    priority=owner.priority.value,
                )
                if owner
                else None
            )
            deviation = (
                "no measurement recorded"
                if kpi.current_value is None
                else f"current {kpi.current_value} vs target {kpi.target_value}"
            )
            attention.append(
                KpiAttention(
                    kpi=kpi,
                    goal=goal_ref,
                    reason=f"KPI {kpi.name!r} is {kpi.status.value}: {deviation}",
                )
            )
        # most severe first (missed before at_risk), then by name for stability
        severity = {KpiStatus.MISSED: 0, KpiStatus.AT_RISK: 1}
        attention.sort(key=lambda a: (severity[a.kpi.status], a.kpi.name))

        all_tasks = await self._tasks.list_for_company(company_id)
        by_status: dict[TaskStatus, list[Task]] = {s: [] for s in TaskStatus}
        for t in all_tasks:
            by_status[t.status].append(t)

        async def _unmet(task_id: TaskId) -> list[str]:
            unmet: list[str] = []
            for dep_id in await self._dependencies.dependencies_of(company_id, task_id):
                dep = next((t for t in all_tasks if t.id == dep_id), None)
                if dep is None or dep.status is not TaskStatus.COMPLETED:
                    unmet.append(str(dep_id))
            return unmet

        failed_tasks = [
            FailedTask(
                task=t,
                last_error=t.metadata.get("last_error"),
                attempts=t.attempts,
                can_retry=t.can_retry(),
            )
            for t in by_status[TaskStatus.FAILED]
        ]
        failed_tasks.sort(key=lambda f: (not f.can_retry, -f.attempts))

        blocked_tasks: list[BlockedTask] = []
        for t in by_status[TaskStatus.PENDING]:
            unmet = await _unmet(t.id)
            if unmet:
                blocked_tasks.append(
                    BlockedTask(
                        task=t,
                        unmet_dependencies=unmet,
                        reason=f"waiting on {len(unmet)} incomplete dependency(ies)",
                    )
                )

        selectable = [
            a for a in await self._agents.list_for_company(company_id) if a.is_selectable()
        ]

        pending_approvals = [
            PendingApprovalRef(
                id=str(a.id),
                action=a.action,
                resource_type=a.resource_type,
                resource_id=a.resource_id,
            )
            for a in await self._approvals.list_pending(company_id)
        ]

        observation = CompanyObservation(
            organization_id=organization_id,
            company_id=company_id,
            generated_at=generated_at,
            active_goals=active_goals[: limits.active_goals],
            goals_total=len(all_goals),
            kpis=all_kpis[: limits.kpis],
            kpis_attention=attention,
            tasks_total=len(all_tasks),
            ready_tasks=by_status[TaskStatus.READY][: limits.ready_tasks],
            pending_tasks=by_status[TaskStatus.PENDING][: limits.pending_tasks],
            running_tasks=by_status[TaskStatus.RUNNING][: limits.running_tasks],
            failed_tasks=failed_tasks[: limits.failed_tasks],
            blocked_tasks=blocked_tasks[: limits.blocked_tasks],
            selectable_agents=selectable[: limits.agents],
            pending_approvals=pending_approvals[: limits.pending_approvals],
            digest="",  # placeholder; computed below
        )
        digest_payload = {
            "company_id": str(company_id),
            "generated_at": generated_at.isoformat(),
            "goals": len(all_goals),
            "kpis": [(str(k.id), k.status.value, k.current_value) for k in observation.kpis],
            "tasks": [(str(t.id), t.status.value, t.attempts) for t in all_tasks],
            "agents": [str(a.id) for a in observation.selectable_agents],
            "pending_approvals": [a.id for a in observation.pending_approvals],
        }
        return dataclasses.replace(observation, digest=_digest(digest_payload))
