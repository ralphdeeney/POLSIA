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
import json
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
    required_output_fields: list[str] = dataclasses.field(default_factory=list)


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
        if not isinstance(self.company_id, CompanyId):
            raise ValidationError("plan company_id must be a CompanyId")
        if not isinstance(self.goal_id, GoalId):
            raise ValidationError("plan goal_id must be a GoalId")
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValidationError("plan objective must not be empty")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValidationError("plan rationale must not be empty")
        if not isinstance(self.constraints, dict):
            raise ValidationError("plan constraints must be an object")
        try:
            json.dumps(self.constraints, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValidationError("plan constraints must contain valid JSON") from exc
        if not isinstance(self.tasks, list):
            raise ValidationError("plan tasks must be a list")
        n = len(self.tasks)
        if n == 0 or n > 100:
            raise ValidationError("plan must contain between 1 and 100 tasks")
        for idx, task in enumerate(self.tasks):
            if not isinstance(task, PlanTaskDraft):
                raise ValidationError(f"task {idx} must be a PlanTaskDraft")
            if not isinstance(task.title, str) or not task.title.strip() or len(task.title) > 255:
                raise ValidationError(f"task {idx} title must contain 1 to 255 characters")
            if task.description is not None and (
                not isinstance(task.description, str) or len(task.description) > 2000
            ):
                raise ValidationError(
                    f"task {idx} description must be a string of at most 2000 characters"
                )
            if not isinstance(task.metadata, dict):
                raise ValidationError(f"task {idx} metadata must be an object")
            try:
                json.dumps(task.metadata, allow_nan=False)
            except (TypeError, ValueError, RecursionError) as exc:
                raise ValidationError(f"task {idx} metadata must contain valid JSON") from exc
            if type(task.requires_approval) is not bool:
                raise ValidationError(f"task {idx} requires_approval must be a boolean")
            if task.assigned_skill is not None and (
                not isinstance(task.assigned_skill, str)
                or not task.assigned_skill.strip()
                or len(task.assigned_skill) > 255
            ):
                raise ValidationError(f"task {idx} assigned_skill must contain 1 to 255 characters")
            if task.verification is not None:
                if not isinstance(task.verification, VerificationCriteria):
                    raise ValidationError(f"task {idx} verification criteria are malformed")
                if (
                    not isinstance(task.verification.expected_outcome, str)
                    or not task.verification.expected_outcome.strip()
                ):
                    raise ValidationError(
                        f"task {idx} verification expected_outcome must not be empty"
                    )
                for name, value in (
                    ("success_metric", task.verification.success_metric),
                    ("kpi_name", task.verification.kpi_name),
                ):
                    if value is not None and (
                        not isinstance(value, str) or not value.strip() or len(value) > 255
                    ):
                        raise ValidationError(
                            f"task {idx} verification {name} must contain 1 to 255 characters"
                        )
                if not isinstance(task.verification.required_output_fields, list) or any(
                    not isinstance(field, str) or not field.strip() or len(field) > 100
                    for field in task.verification.required_output_fields
                ):
                    raise ValidationError(f"task {idx} verification output fields are malformed")
            if not isinstance(task.depends_on_indexes, list) or any(
                type(dep) is not int for dep in task.depends_on_indexes
            ):
                raise ValidationError(f"task {idx} dependency indexes must be integers")
            if len(set(task.depends_on_indexes)) != len(task.depends_on_indexes):
                raise ValidationError(f"task {idx} has duplicate dependency indexes")
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
                    "verification": (
                        {
                            "expected_outcome": t.verification.expected_outcome,
                            "success_metric": t.verification.success_metric,
                            "kpi_name": t.verification.kpi_name,
                            "required_output_fields": list(t.verification.required_output_fields),
                        }
                        if t.verification is not None
                        else None
                    ),
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
            if not isinstance(getattr(raw, "metadata", None), dict):
                raise ValidationError(f"task {raw.title!r} metadata must be an object")
            metadata = dict(raw.metadata)
            raw_dependencies = getattr(raw, "depends_on_indexes", None)
            if not isinstance(raw_dependencies, list):
                raise ValidationError(
                    f"task {raw.title!r} depends_on_indexes must be a list of integers"
                )
            required_fields = metadata.get("required_output_fields", [])
            if not isinstance(required_fields, list) or any(
                not isinstance(field, str) or not field.strip() for field in required_fields
            ):
                raise ValidationError(f"task {raw.title!r} has malformed required_output_fields")
            requires_approval = metadata.get("requires_approval", False)
            if type(requires_approval) is not bool:
                raise ValidationError(f"task {raw.title!r} requires_approval must be a boolean")
            drafts.append(
                PlanTaskDraft(
                    title=raw.title,
                    description=raw.description,
                    depends_on_indexes=list(raw_dependencies),
                    assigned_skill=raw.assigned_skill,
                    verification=VerificationCriteria(
                        expected_outcome=raw.title,
                        success_metric=str(metadata.get("success_metric"))
                        if metadata.get("success_metric")
                        else None,
                        required_output_fields=list(required_fields),
                    ),
                    requires_approval=requires_approval,
                    metadata=metadata,
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
        plan.validate()
        if plan.company_id != company_id:
            raise ValidationError("plan company does not match materialization scope")
        await self._scope.require_company(actor, organization_id, company_id)
        goal = await self._goals.get(company_id, plan.goal_id)
        if goal is None:
            raise NotFoundError(f"goal {plan.goal_id} not found in company {company_id}")
        # Normalize and copy every draft before the first write. This keeps the
        # preflight guarantee even when callers retain mutable plan structures.
        prepared: list[tuple[PlanTaskDraft, dict[str, Any], tuple[int, ...]]] = []
        for draft in plan.tasks:
            metadata = json.loads(json.dumps(draft.metadata, allow_nan=False))
            metadata_requires = metadata.get("requires_approval")
            if metadata_requires is not None and type(metadata_requires) is not bool:
                raise ValidationError(
                    f"task {draft.title!r} metadata requires_approval must be a boolean"
                )
            if draft.requires_approval:
                metadata["requires_approval"] = True
            if draft.verification is not None:
                metadata["verification"] = {
                    "expected_outcome": draft.verification.expected_outcome,
                    "success_metric": draft.verification.success_metric,
                    "kpi_name": draft.verification.kpi_name,
                    "required_output_fields": list(draft.verification.required_output_fields),
                }
            prepared.append((draft, metadata, tuple(draft.depends_on_indexes)))

        created: list[Task] = []
        for draft, metadata, dependency_indexes in prepared:
            depends_on = [created[idx].id for idx in dependency_indexes]
            task = await tasks_service.create_task(
                actor,
                organization_id,
                company_id,
                title=draft.title,
                description=draft.description,
                goal_id=plan.goal_id,
                assigned_skill=draft.assigned_skill,
                metadata=metadata,
                depends_on=depends_on,
            )
            created.append(task)
        return created
