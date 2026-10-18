"""Plan materialization preflight and dependency tests."""

from __future__ import annotations

from typing import Any

import pytest
from polsia.core.planning.entities import Goal
from polsia.core.shared.errors import NotFoundError, ValidationError
from polsia.core.shared.ids import CompanyId, GoalId, OrganizationId
from polsia.core.tasks.entities import Task
from polsia.core.tenancy.entities import Company, Organization, OrgMembership, User
from polsia.core.tenancy.values import MembershipRole
from polsia.services.planning_engine import (
    Plan,
    PlanningEngine,
    PlanTaskDraft,
    VerificationCriteria,
)


class _Goals:
    def __init__(self, goal: Goal | None) -> None:
        self.goal = goal

    async def get(self, company_id: CompanyId, goal_id: GoalId) -> Goal | None:
        if self.goal is None or self.goal.company_id != company_id or self.goal.id != goal_id:
            return None
        return self.goal


class _Companies:
    def __init__(self, company: Company) -> None:
        self.company = company

    async def get(self, organization_id: OrganizationId, company_id: CompanyId) -> Company | None:
        if self.company.organization_id != organization_id or self.company.id != company_id:
            return None
        return self.company


class _Memberships:
    async def get(self, organization_id: OrganizationId, user_id: object) -> OrgMembership:
        return OrgMembership.create(
            organization_id=organization_id,
            user_id=user_id,  # type: ignore[arg-type]
            role=MembershipRole.OWNER,
        )


class _TaskWriter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_task(self, *args: Any, **kwargs: Any) -> Task:
        self.calls.append(kwargs)
        return Task.create(
            company_id=args[2],
            title=kwargs["title"],
            goal_id=kwargs.get("goal_id"),
            metadata=kwargs.get("metadata"),
        )


def _context(goal: Goal | None = None) -> tuple[PlanningEngine, User, Organization, Company, Goal]:
    user = User.register(
        email="plan-owner@example.com", hashed_password="x", display_name="Plan owner"
    )
    organization = Organization.create(name="Plan Org", slug="plan-org", owner_user_id=user.id)
    company = Company.create(organization_id=organization.id, name="Plan Co", slug="plan-co")
    goal = goal or Goal.create(company_id=company.id, title="Plan goal")
    engine = PlanningEngine(
        object(),  # type: ignore[arg-type]
        goals=_Goals(goal),  # type: ignore[arg-type]
        companies=_Companies(company),  # type: ignore[arg-type]
        memberships=_Memberships(),  # type: ignore[arg-type]
    )
    return engine, user, organization, company, goal


def _plan(company_id: CompanyId, goal_id: GoalId, drafts: list[PlanTaskDraft]) -> Plan:
    return Plan(
        company_id=company_id,
        goal_id=goal_id,
        objective="Deliver the goal",
        rationale="Validated plan",
        tasks=drafts,
    )


class TestPlanMaterializationPreflight:
    @pytest.mark.parametrize(
        "drafts",
        [
            [PlanTaskDraft(title="first", depends_on_indexes=[1])],
            [PlanTaskDraft(title="first", depends_on_indexes=[1]), PlanTaskDraft(title="later")],
            [PlanTaskDraft(title="bad metadata", metadata={"not_json": {1, 2}})],
            [PlanTaskDraft(title="bad approval", requires_approval=1)],
            [PlanTaskDraft(title="bad approval metadata", metadata={"requires_approval": "false"})],
            [
                PlanTaskDraft(title="valid"),
                PlanTaskDraft(
                    title="bad verification",
                    verification=VerificationCriteria(
                        expected_outcome="done",
                        required_output_fields="not-a-list",  # type: ignore[arg-type]
                    ),
                ),
            ],
        ],
    )
    async def test_invalid_dependencies_fail_before_any_task_write(
        self, drafts: list[PlanTaskDraft]
    ) -> None:
        engine, user, organization, company, goal = _context()
        writer = _TaskWriter()
        with pytest.raises(ValidationError):
            await engine.materialize(
                user,
                organization.id,
                company.id,
                _plan(company.id, goal.id, drafts),
                tasks_service=writer,  # type: ignore[arg-type]
            )
        assert writer.calls == []

    async def test_plan_company_mismatch_fails_before_task_write(self) -> None:
        engine, user, organization, company, goal = _context()
        writer = _TaskWriter()
        plan = _plan(CompanyId.generate(), goal.id, [PlanTaskDraft(title="task")])
        with pytest.raises(ValidationError, match="plan company"):
            await engine.materialize(
                user,
                organization.id,
                company.id,
                plan,
                tasks_service=writer,  # type: ignore[arg-type]
            )
        assert writer.calls == []

    @pytest.mark.parametrize("goal_mode", ["missing", "foreign"])
    async def test_missing_or_foreign_goal_fails_before_task_write(self, goal_mode: str) -> None:
        foreign_goal = (
            Goal.create(company_id=CompanyId.generate(), title="Foreign goal")
            if goal_mode == "foreign"
            else None
        )
        engine, user, organization, company, goal = _context(foreign_goal)
        writer = _TaskWriter()
        goal_id = GoalId.generate() if goal_mode == "missing" else goal.id
        plan = _plan(company.id, goal_id, [PlanTaskDraft(title="task")])
        with pytest.raises(NotFoundError):
            await engine.materialize(
                user,
                organization.id,
                company.id,
                plan,
                tasks_service=writer,  # type: ignore[arg-type]
            )
        assert writer.calls == []

    async def test_malformed_later_draft_fails_before_first_task_write(self) -> None:
        engine, user, organization, company, goal = _context()
        writer = _TaskWriter()
        plan = _plan(
            company.id,
            goal.id,
            [PlanTaskDraft(title="valid"), PlanTaskDraft(title="x" * 256)],
        )
        with pytest.raises(ValidationError):
            await engine.materialize(
                user,
                organization.id,
                company.id,
                plan,
                tasks_service=writer,  # type: ignore[arg-type]
            )
        assert writer.calls == []

    async def test_valid_dependency_chain_materializes_in_order(self) -> None:
        engine, user, organization, company, goal = _context()
        writer = _TaskWriter()
        created = await engine.materialize(
            user,
            organization.id,
            company.id,
            _plan(
                company.id,
                goal.id,
                [
                    PlanTaskDraft(
                        title="first",
                        requires_approval=True,
                        verification=VerificationCriteria(
                            expected_outcome="first complete",
                            success_metric="generic metric",
                            kpi_name="generic KPI",
                            required_output_fields=["result"],
                        ),
                    ),
                    PlanTaskDraft(title="second", depends_on_indexes=[0]),
                ],
            ),
            tasks_service=writer,  # type: ignore[arg-type]
        )
        assert len(created) == 2
        assert writer.calls[0]["depends_on"] == []
        assert writer.calls[0]["metadata"]["requires_approval"] is True
        assert writer.calls[0]["metadata"]["verification"] == {
            "expected_outcome": "first complete",
            "success_metric": "generic metric",
            "kpi_name": "generic KPI",
            "required_output_fields": ["result"],
        }
        assert writer.calls[1]["depends_on"] == [created[0].id]
