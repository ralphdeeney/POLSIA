"""Planning domain (Phase 3, mandate §8): typed plans + the planning engine.

A Plan is structured data: objective, rationale, ordered task drafts with
dependency wiring, verification criteria, approval requirements, constraints.
The engine wraps the existing ``OrchestratorPort`` (created in Phase 2) and
enriches its decomposed draft list into a full Plan — the port boundary is
preserved, no natural-language-only planning.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Protocol

from polsia.core.planning.ports import GoalRepository
from polsia.core.shared.errors import NotFoundError, ValidationError
from polsia.core.shared.ids import CompanyId, GoalId
from polsia.core.tasks.entities import Task
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.planning import _CompanyScope
from polsia.services.tasks import TaskService


@dataclasses.dataclass(frozen=True)
class VerificationCriteria:
    """What success looks like for a plan (mandate §8 / §11)."""

    expected_outcome: str
    success_metric: str | None = None  # generic; interpreted by later stages
    kpi_name: str | None = None  # optional generic KPI to observe


@dataclasses.dataclass(frozen=True)
class PlanTaskDraft:
    title: str
    description: str | None = None
    depends_on_indexes: list[int] = dataclasses.field(default_factory=list)
    assigned_skill: str | None = None
    verification: VerificationCriteria | None = None
    requires_approval: bool = False
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Plan:
    """A structured plan to achieve an objective. Deterministic, inspectable."""

    company_id: CompanyId
    goal_id: GoalId
    objective: str
    rationale: str
    tasks: list[PlanTaskDraft]
    constraints: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=lambda: dt.datetime.now(dt.UTC))

    def validate(self) -> None:
        if not self.objective.strip():
            raise ValidationError("plan objective must not be empty")
        n = len(self.tasks)
        # dependency indexes must be within range
        for idx, task in enumerate(self.tasks):
            for dep in task.depends_on_indexes:
                if dep < 0 or dep >= n:
                    raise ValidationError(
                        f"task {idx} ({task.title!r}) depends on invalid index {dep}"
                    )
        # the dependency graph must be acyclic (each task depends on earlier
        # indexes only, by convention — enforce it, preventing self/cycles)
        for idx, task in enumerate(self.tasks):
            if any(dep >= idx for dep in task.depends_on_indexes):
                raise ValidationError(
                    f"plan tasks must depend only on earlier tasks; task {idx} "
                    f"({task.title!r}) depends on {task.depends_on_indexes}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": str(self.company_id),
            "goal_id": str(self.goal_id),
            "objective": self.objective,
            "rationale": self.rationale,
            "constraints": self.constraints,
            "tasks": [
                {
                    "title": t.title,
                    "depends_on_indexes": t.depends_on_indexes,
                    "requires_approval": t.requires_approval,
                    "assigned_skill": t.assigned_skill,
                }
                for t in self.tasks
            ],
            "created_at": self.created_at.isoformat(),
        }


class PlanEnginePort(Protocol):
    """Provider-neutral planning seam (default: DeterministicOrchestrator)."""

    async def decompose(
        self,
        *,
        company_id: CompanyId,
        goal_id: GoalId,
        goal_title: str,
        goal_description: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> Any: ...


class PlanningEngine:
    """Turns a goal into a validated, structured Plan.

    Uses the injected OrchestratorPort for decomposition (Phase 2 boundary)
    and annotates each task with generic verification criteria. No business
    semantics are added here.
    """

    def __init__(
        self,
        orchestrator: PlanEnginePort,
        *,
        goals: GoalRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
    ) -> None:
        self._orchestrator = orchestrator
        self._goals = goals
        self._scope = _CompanyScope(companies, memberships)

    async def plan_goal(
        self,
        actor: User,
        organization_id: Any,
        company_id: CompanyId,
        goal_id: GoalId,
        *,
        objective: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> Plan:
        await self._scope.require_company(actor, organization_id, company_id)
        goal = await self._goals.get(company_id, goal_id)
        if goal is None:
            raise NotFoundError(f"goal {goal_id} not found")
        decomposition = await self._orchestrator.decompose(
            company_id=company_id,
            goal_id=goal_id,
            goal_title=goal.title,
            goal_description=goal.description,
            context=context,
        )
        drafts: list[PlanTaskDraft] = []
        for raw in decomposition.tasks:
            drafts.append(
                PlanTaskDraft(
                    title=raw.title,
                    description=raw.description,
                    depends_on_indexes=list(raw.depends_on_indexes),
                    assigned_skill=raw.assigned_skill,
                    verification=VerificationCriteria(
                        expected_outcome=raw.title,
                        success_metric=str(raw.metadata.get("success_metric"))
                        if raw.metadata.get("success_metric")
                        else None,
                    ),
                    requires_approval=bool(raw.metadata.get("requires_approval")),
                    metadata=dict(raw.metadata or {}),
                )
            )
        plan = Plan(
            company_id=company_id,
            goal_id=goal_id,
            objective=objective or goal.title,
            rationale=decomposition.rationale or "goal-derived plan",
            tasks=drafts,
            constraints={"generated_by": getattr(self._orchestrator, "name", "unknown")},
        )
        plan.validate()
        return plan

    async def materialize(
        self,
        actor: User,
        organization_id: Any,
        company_id: CompanyId,
        plan: Plan,
        *,
        tasks_service: TaskService,
    ) -> list[Task]:
        """Persist a Plan's task drafts as real Tasks (with dependencies)."""
        created: list[Task] = []
        for draft in plan.tasks:
            depends_on = [created[idx].id for idx in draft.depends_on_indexes if idx < len(created)]
            task = await tasks_service.create_task(
                actor,
                organization_id,
                company_id,
                title=draft.title,
                description=draft.description,
                goal_id=plan.goal_id,
                assigned_skill=draft.assigned_skill,
                metadata=draft.metadata,
                depends_on=depends_on,
            )
            created.append(task)
        return created
