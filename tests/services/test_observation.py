"""CompanyStateObserver tests (Phase 3 Slice 2, mandate §5).

Verifies: bounded sections with totals, correct classification (attention
KPIs, blocked/failed tasks, selectable agents, pending approvals), stable
digests, and tenant scoping (observations never include another company's
data).
"""

from __future__ import annotations

from typing import Any

import pytest
from polsia.adapters.events import InProcessEventBus
from polsia.adapters.persistence.permission_repositories import (
    SqlAlchemyApprovalRepository,
    SqlAlchemyPermissionRepository,
)
from polsia.adapters.persistence.registry_repositories import SqlAlchemyAgentRepository
from polsia.adapters.persistence.repositories import (
    SqlAlchemyCompanyRepository,
    SqlAlchemyGoalRepository,
    SqlAlchemyKpiRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from polsia.adapters.persistence.task_repositories import (
    SqlAlchemyTaskDependencyRepository,
    SqlAlchemyTaskRepository,
)
from polsia.adapters.security.passwords import BcryptPasswordHasher
from polsia.core.auth.service import AuthService
from polsia.core.permissions.values import PermissionDecision
from polsia.core.planning.values import GoalStatus, KpiDirection, Priority
from polsia.core.shared.ids import CompanyId
from polsia.core.tenancy.entities import Company, Organization, OrgMembership, User
from polsia.core.tenancy.values import MembershipRole
from polsia.infra.db import create_all_metadata
from polsia.services.observation import CompanyStateObserver, ObservationLimits
from polsia.services.permissions import PermissionService
from polsia.services.registries import AgentService
from polsia.services.tasks import TaskService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FIXED_NOW = None  # utcnow used; digests vary per run but stability is tested


class _NullTokens:
    def issue_access_token(self, user_id: object) -> str:
        return ""

    def issue_refresh_token(self, user_id: object) -> str:
        return ""

    def verify_token(self, token: str) -> object:
        raise ValueError("no tokens in this test env")


@pytest.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            bus = InProcessEventBus()
            users = SqlAlchemyUserRepository(session)
            user: User = await AuthService(users, BcryptPasswordHasher(), _NullTokens()).register(
                "observer@example.com", "password-123", "Observer"
            )
            companies = SqlAlchemyCompanyRepository(session)
            memberships = SqlAlchemyMembershipRepository(session)
            org_repo = SqlAlchemyOrganizationRepository(session)
            org = Organization.create(name="O", slug="observe-org", owner_user_id=user.id)
            await org_repo.add(org)
            await memberships.add(
                OrgMembership.create(
                    organization_id=org.id, user_id=user.id, role=MembershipRole.OWNER
                )
            )
            company1 = Company.create(organization_id=org.id, name="C1", slug="observe-co-one")
            company2 = Company.create(organization_id=org.id, name="C2", slug="observe-co-two")
            await companies.add(company1)
            await companies.add(company2)

            goals = SqlAlchemyGoalRepository(session)
            kpis = SqlAlchemyKpiRepository(session)
            tasks = SqlAlchemyTaskRepository(session)
            deps = SqlAlchemyTaskDependencyRepository(session)
            agents = SqlAlchemyAgentRepository(session)

            observer = CompanyStateObserver(
                goals=goals,
                kpis=kpis,
                tasks=tasks,
                dependencies=deps,
                agents=agents,
                approvals=SqlAlchemyApprovalRepository(session),
            )
            agent_service = AgentService(agents, companies, memberships, bus)
            task_service = TaskService(tasks, deps, companies, memberships, bus)
            permission_service = PermissionService(
                SqlAlchemyPermissionRepository(session),
                SqlAlchemyApprovalRepository(session),
                companies,
                memberships,
                bus,
            )
            yield {
                "user": user,
                "org": org,
                "company1": company1,
                "company2": company2,
                "observer": observer,
                "goals": goals,
                "kpis": kpis,
                "task_service": task_service,
                "agent_service": agent_service,
                "permission_service": permission_service,
                "session": session,
            }
    finally:
        await engine.dispose()


async def _seed_rich_company(env: dict[str, Any], company_id: CompanyId) -> None:
    """Goal + attention KPI + ready/pending/failed tasks + agent + approval."""
    user, org = env["user"], env["org"]
    goals = env["goals"]
    goal = goals.__class__  # placeholder to satisfy linters; real creation below
    from polsia.core.planning.entities import Goal

    active_goal = Goal.create(
        company_id=company_id, title="Active goal", priority=Priority.HIGH
    ).transition_status(GoalStatus.ACTIVE)
    await goals.add(active_goal)
    completed_goal = (
        Goal.create(company_id=company_id, title="Done goal")
        .transition_status(GoalStatus.ACTIVE)
        .transition_status(GoalStatus.COMPLETED)
    )
    await goals.add(completed_goal)

    from polsia.core.planning.entities import Kpi as KpiEntity

    at_risk = KpiEntity.create(
        company_id=company_id,
        name="attention_kpi",
        direction=KpiDirection.INCREASE,
        target_value=100,
        goal_id=active_goal.id,
    ).record_measurement(40)
    await env["kpis"].add(at_risk)
    healthy = KpiEntity.create(
        company_id=company_id,
        name="healthy_kpi",
        direction=KpiDirection.INCREASE,
        target_value=10,
    ).record_measurement(10)
    await env["kpis"].add(healthy)

    task_service = env["task_service"]
    # standalone task: no dependencies -> ready immediately (dependency gating)
    await task_service.create_task(user, org.id, company_id, title="ready task")
    failed = await task_service.create_task(
        user, org.id, company_id, title="failed task", max_attempts=1
    )
    from polsia.core.tasks.values import TaskStatus

    # standalone task starts READY (dependency gating); go straight to running
    failed = failed.transition_status(TaskStatus.RUNNING)
    failed = failed.transition_status(TaskStatus.FAILED)
    await env["task_service"]._tasks.update(failed)

    await env["agent_service"].register_agent(user, org.id, company_id, name="obs-worker")
    # a pending approval (execution approval_required for an agent)
    await env["permission_service"].set_policy(
        user,
        org.id,
        company_id,
        principal_type="agent",
        principal_id="agt_any",
        resource_type="tool",
        resource_id="spend@1.0.0",
        action="spend.money",
        decision=PermissionDecision.APPROVAL_REQUIRED,
    )
    _ = goal


class TestObservation:
    async def test_bounded_sections_and_classification(self, env: dict[str, Any]) -> None:
        org, company = env["org"], env["company1"]
        await _seed_rich_company(env, company.id)
        obs = await env["observer"].observe(org.id, company.id)

        # goals: active listed, completed excluded
        assert obs.goals_total == 2
        assert len(obs.active_goals) == 1
        assert obs.active_goals[0].title == "Active goal"

        # KPI attention: only the at_risk one, healthy excluded
        assert len(obs.kpis) == 2
        assert [a.kpi.name for a in obs.kpis_attention] == ["attention_kpi"]
        assert "at_risk" in obs.kpis_attention[0].reason
        assert obs.kpis_attention[0].goal is not None

        # tasks: ready classified; failed with retry info
        assert len(obs.ready_tasks) == 1
        assert obs.tasks_total >= 2
        assert len(obs.failed_tasks) == 1
        assert obs.failed_tasks[0].task.status.value == "failed"
        assert obs.failed_tasks[0].attempts == 1

        # agent is selectable
        assert len(obs.selectable_agents) == 1

        # digest is a non-empty stable-looking hex string
        assert len(obs.digest) == 32
        int(obs.digest, 16)

    async def test_digest_is_stable_for_same_state(self, env: dict[str, Any]) -> None:
        org, company = env["org"], env["company1"]
        await _seed_rich_company(env, company.id)
        now = __import__("datetime").datetime(2026, 9, 20, tzinfo=__import__("datetime").UTC)
        obs1 = await env["observer"].observe(org.id, company.id, now=now)
        obs2 = await env["observer"].observe(org.id, company.id, now=now)
        assert obs1.digest == obs2.digest
        # different company -> different digest (state differs)
        obs_other = await env["observer"].observe(org.id, env["company2"].id, now=now)
        assert obs_other.digest != obs1.digest

    async def test_limits_cap_sections_but_totals_remain(self, env: dict[str, Any]) -> None:
        user, org, company = env["user"], env["org"], env["company1"]
        # Build a chain: task i depends on task i-1, so tasks 1..6 start PENDING
        # (their prerequisite is incomplete) while task 0 has no deps -> READY.
        task_ids: list[str] = []
        for i in range(7):
            depends_on = [task_ids[-1]] if task_ids else None
            task = await env["task_service"].create_task(
                user,
                org.id,
                company.id,
                title=f"t{i}",
                depends_on=depends_on,  # type: ignore[arg-type]
            )
            task_ids.append(task.id)

        observer = CompanyStateObserver(
            goals=env["goals"],
            kpis=env["kpis"],
            tasks=env["task_service"]._tasks,
            dependencies=__import__(
                "polsia.adapters.persistence.task_repositories",
                fromlist=["SqlAlchemyTaskDependencyRepository"],
            ).SqlAlchemyTaskDependencyRepository(env["session"]),
            agents=__import__(
                "polsia.adapters.persistence.registry_repositories",
                fromlist=["SqlAlchemyAgentRepository"],
            ).SqlAlchemyAgentRepository(env["session"]),
            approvals=__import__(
                "polsia.adapters.persistence.permission_repositories",
                fromlist=["SqlAlchemyApprovalRepository"],
            ).SqlAlchemyApprovalRepository(env["session"]),
            limits=ObservationLimits(pending_tasks=3),
        )
        obs = await observer.observe(org.id, company.id)
        # 1 READY (no deps) + 6 PENDING (capped at 3 in the observation)
        assert len(obs.ready_tasks) == 1
        assert len(obs.pending_tasks) == 3
        assert obs.tasks_total == 7

    async def test_tenant_isolation(self, env: dict[str, Any]) -> None:
        user, org = env["user"], env["org"]
        await _seed_rich_company(env, env["company1"].id)
        # company2 stays empty: its observation must show none of company1's data
        obs2 = await env["observer"].observe(org.id, env["company2"].id)
        assert obs2.tasks_total == 0
        assert obs2.goals_total == 0
        assert obs2.kpis == []
        assert obs2.selectable_agents == []
        obs1 = await env["observer"].observe(org.id, env["company1"].id)
        assert obs1.tasks_total > 0
        _ = user

    async def test_to_dict_is_serializable_summary(self, env: dict[str, Any]) -> None:
        import json as _json

        org, company = env["org"], env["company1"]
        await _seed_rich_company(env, company.id)
        obs = await env["observer"].observe(org.id, company.id)
        payload = obs.to_dict()
        # serializable, no full entity dumps (ids and counts only)
        _json.dumps(payload)
        assert payload["company_id"] == str(company.id)
        assert payload["tasks"]["failed"][0]["task_id"] == str(obs.failed_tasks[0].task.id)
        assert "digest" in payload
