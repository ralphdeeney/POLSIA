"""Phase 3 integration: the complete autonomous execution loop.

    COMPANY GOAL -> CEO PLAN -> TASK MATERIALIZATION -> AUTHORIZATION
    -> AGENT ASSIGNMENT -> EXECUTION DISPATCH -> RESULT VERIFICATION
    -> EVENT RECORDING -> COMPANY STATE UPDATE -> NEXT PLANNING CYCLE

Exercised end to end through the real services and SQLAlchemy adapters on
SQLite with a deterministic runtime adapter (``MockRuntime``): no external
providers, and no fake production integrations. This is an *isolated*
composition — the production API does not wire or enable autonomous execution
(see ``docs/ARCHITECTURE.md``: runtime composition boundary).
"""

from __future__ import annotations

from typing import Any

import pytest
from polsia.adapters.events import InProcessEventBus
from polsia.adapters.persistence.decision_repositories import SqlAlchemyDecisionRecordRepository
from polsia.adapters.persistence.event_store import SqlAlchemyEventStore
from polsia.adapters.persistence.models import TaskExecutionModel
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
    SqlAlchemyTaskExecutionRepository,
    SqlAlchemyTaskRepository,
)
from polsia.adapters.runtime import DeterministicOrchestrator, MockRuntime
from polsia.core.permissions.errors import ApprovalRequired, PermissionDenied
from polsia.core.permissions.values import PermissionDecision
from polsia.core.planning.entities import Goal
from polsia.core.planning.values import GoalStatus, Priority
from polsia.core.shared.errors import NotFoundError
from polsia.core.tasks.values import TaskStatus
from polsia.core.tenancy.entities import Company, Organization, OrgMembership, User
from polsia.core.tenancy.values import CompanyStatus, MembershipRole
from polsia.infra.db import create_all_metadata
from polsia.services.autonomous_loop import AutonomousLoop
from polsia.services.delegation import DelegationService
from polsia.services.execution_authorization import ExecutionAuthorizationService
from polsia.services.observation import CompanyStateObserver
from polsia.services.orchestration import OrchestrationService
from polsia.services.permissions import ApprovalService, PermissionService
from polsia.services.planning_engine import PlanningEngine
from polsia.services.prioritization import PriorityEngine
from polsia.services.registries import AgentService
from polsia.services.tasks import ExecutionService, TaskService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def loop_env():
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
            store = SqlAlchemyEventStore(session)
            companies = SqlAlchemyCompanyRepository(session)
            memberships = SqlAlchemyMembershipRepository(session)
            goals = SqlAlchemyGoalRepository(session)
            kpis = SqlAlchemyKpiRepository(session)
            tasks_repo = SqlAlchemyTaskRepository(session)
            deps = SqlAlchemyTaskDependencyRepository(session)
            agents = SqlAlchemyAgentRepository(session)
            executions_repo = SqlAlchemyTaskExecutionRepository(session)

            user = User.register(email="loop@example.com", hashed_password="x", display_name="Loop")
            org = Organization.create(name="Loop Org", slug="loop-org", owner_user_id=user.id)
            company = Company.create(
                organization_id=org.id, name="Loop Co", slug="loop-co"
            ).transition_status(CompanyStatus.ACTIVE)
            other = Company.create(
                organization_id=org.id, name="Other Co", slug="loop-other"
            ).transition_status(CompanyStatus.ACTIVE)
            await SqlAlchemyUserRepository(session).add(user)
            await SqlAlchemyOrganizationRepository(session).add(org)
            await companies.add(company)
            await companies.add(other)
            await memberships.add(
                OrgMembership.create(
                    organization_id=org.id, user_id=user.id, role=MembershipRole.OWNER
                )
            )

            task_service = TaskService(tasks_repo, deps, companies, memberships, bus)
            permission_repo = SqlAlchemyPermissionRepository(session)
            approval_repo = SqlAlchemyApprovalRepository(session)
            permission_service = PermissionService(
                permission_repo, approval_repo, companies, memberships, bus
            )
            approval_service = ApprovalService(approval_repo, companies, memberships, bus)
            authorization = ExecutionAuthorizationService(
                tasks=task_service,
                agents=agents,
                companies=companies,
                memberships=memberships,
                permissions=permission_service,
                approvals=approval_service,
            )
            execution_service = ExecutionService(
                tasks_repo,
                executions_repo,
                deps,
                companies,
                memberships,
                bus,
                authorization=authorization,
            )
            agent_service = AgentService(agents, companies, memberships, bus)
            orchestration = OrchestrationService(
                tasks=task_service,
                executions=execution_service,
                orchestrator=DeterministicOrchestrator(),
                runtime=MockRuntime(),
                agents=agents,
                kpis=kpis,
                companies=companies,
                memberships=memberships,
                bus=bus,
                store=store,
                permissions=permission_service,
                approvals=approval_service,
            )
            planning = PlanningEngine(
                DeterministicOrchestrator(),
                goals=goals,
                companies=companies,
                memberships=memberships,
            )
            observer = CompanyStateObserver(goals, kpis, tasks_repo, deps, agents, approval_repo)
            loop = AutonomousLoop(
                observer=observer,
                prioritizer=PriorityEngine(),
                delegation=DelegationService(task_service, agents, companies, memberships, bus),
                executions=execution_service,
                orchestration=orchestration,
                tasks=task_service,
                records=SqlAlchemyDecisionRecordRepository(session),
                companies=companies,
                memberships=memberships,
                max_actions=3,
            )
            yield {
                "session": session,
                "user": user,
                "org": org,
                "company": company,
                "other": other,
                "goals": goals,
                "tasks": task_service,
                "agents": agent_service,
                "agents_repo": agents,
                "permissions": permission_service,
                "authorization": authorization,
                "orchestration": orchestration,
                "planning": planning,
                "observer": observer,
                "loop": loop,
                "store": store,
            }
    finally:
        await engine.dispose()


async def _enable_agent(env: dict[str, Any], decision: PermissionDecision) -> None:
    """Register one agent and set the company-wide execute policy."""
    await env["agents"].register_agent(
        env["user"], env["org"].id, env["company"].id, name="loop-worker"
    )
    for agent in await env["agents_repo"].list_for_company(env["company"].id):
        await env["permissions"].set_policy(
            env["user"],
            env["org"].id,
            env["company"].id,
            principal_type="agent",
            principal_id=str(agent.id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=decision,
        )


async def _execution_count(env: dict[str, Any]) -> int:
    rows = (await env["session"].execute(select(TaskExecutionModel))).scalars().all()
    return len(list(rows))


class TestAutonomousExecutionLoop:
    async def test_full_goal_to_next_cycle_flow(self, loop_env: dict[str, Any]) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        await _enable_agent(env, PermissionDecision.ALLOW)

        # 1. Company A creates a goal.
        goal = Goal.create(
            company_id=company.id, title="Launch a business", priority=Priority.HIGH
        ).transition_status(GoalStatus.ACTIVE)
        await env["goals"].add(goal)

        # 2. The planning service generates a valid, structured plan.
        plan = await env["planning"].plan_goal(
            user,
            org.id,
            company.id,
            goal.id,
            context={"objectives": ["research the market", "launch the website"]},
        )
        plan.validate()
        assert len(plan.tasks) == 2

        # 3. The plan materializes into real tasks (with dependency wiring).
        created = await env["planning"].materialize(
            user, org.id, company.id, plan, tasks_service=env["tasks"]
        )
        first, second = created
        assert first.status is TaskStatus.READY
        assert second.status is TaskStatus.PENDING

        # 4. Authorization evaluates execution permission for the exact task.
        authorized_task, agent = await env["authorization"].authorize_task(
            user, org.id, company.id, first.id
        )
        assert authorized_task.id == first.id
        assert agent.is_selectable()

        # 5-7. Dispatch, execute through the deterministic runtime, verify.
        task, execution, result = await env["orchestration"].execute_task(
            user, org.id, company.id, first.id
        )
        assert result.is_success()
        assert task.status is TaskStatus.COMPLETED
        assert execution.status.value == "completed"

        # 8. The event is persisted durably.
        events = await env["store"].list_for_company(org.id, company.id)
        assert any(event.type == "orchestration.executed" for event in events)

        # 9. Company state update: completing the first task released the second.
        observation = await env["observer"].observe(org.id, company.id)
        assert observation.tasks_total == 2
        assert [t.id for t in observation.ready_tasks] == [second.id]
        assert observation.pending_tasks == []
        assert observation.digest

        # 10. The next planning cycle observes the completed work and advances.
        result_cycle = await env["loop"].run(user, org.id, company.id)
        assert result_cycle.actions_taken == 1
        assert result_cycle.stop_reason == "no_eligible_work"
        final = await env["observer"].observe(org.id, company.id)
        assert final.ready_tasks == []
        assert final.tasks_total == 2
        refreshed = await env["tasks"].get_task(user, org.id, company.id, second.id)
        assert refreshed.status is TaskStatus.COMPLETED

    async def test_company_isolation(self, loop_env: dict[str, Any]) -> None:
        env = loop_env
        user, org, company, other = env["user"], env["org"], env["company"], env["other"]
        await _enable_agent(env, PermissionDecision.ALLOW)
        task = await env["tasks"].create_task(user, org.id, company.id, title="company A task")

        # Company B cannot act on or observe Company A's data.
        with pytest.raises(NotFoundError):
            await env["authorization"].authorize_task(user, org.id, other.id, task.id)
        with pytest.raises(NotFoundError):
            await env["orchestration"].execute_task(user, org.id, other.id, task.id)
        other_obs = await env["observer"].observe(org.id, other.id)
        assert other_obs.tasks_total == 0
        assert other_obs.goals_total == 0
        assert await _execution_count(env) == 0

    async def test_denied_action_creates_no_execution_attempt(
        self, loop_env: dict[str, Any]
    ) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        await _enable_agent(env, PermissionDecision.DENY)
        task = await env["tasks"].create_task(user, org.id, company.id, title="must not run")

        with pytest.raises(PermissionDenied):
            await env["orchestration"].execute_task(user, org.id, company.id, task.id)
        assert await _execution_count(env) == 0
        refreshed = await env["tasks"].get_task(user, org.id, company.id, task.id)
        assert refreshed.status is TaskStatus.READY

    async def test_approval_required_does_not_execute_prematurely(
        self, loop_env: dict[str, Any]
    ) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        await _enable_agent(env, PermissionDecision.APPROVAL_REQUIRED)
        task = await env["tasks"].create_task(user, org.id, company.id, title="needs approval")

        with pytest.raises(ApprovalRequired):
            await env["orchestration"].execute_task(user, org.id, company.id, task.id)
        assert await _execution_count(env) == 0
        observation = await env["observer"].observe(org.id, company.id)
        assert len(observation.pending_approvals) == 1

    async def test_failed_execution_does_not_become_successful(
        self, loop_env: dict[str, Any]
    ) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        await _enable_agent(env, PermissionDecision.ALLOW)
        task = await env["tasks"].create_task(
            user, org.id, company.id, title="will fail", input={"fail": True}
        )

        refreshed, execution, result = await env["orchestration"].execute_task(
            user, org.id, company.id, task.id
        )
        assert not result.is_success()
        assert refreshed.status is TaskStatus.FAILED
        assert execution.status.value == "failed"

    async def test_recovery_does_not_bypass_authorization(self, loop_env: dict[str, Any]) -> None:
        """A retry of an exhausted task still goes through the permission gate."""
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        await _enable_agent(env, PermissionDecision.ALLOW)
        task = await env["tasks"].create_task(
            user, org.id, company.id, title="exhausts", input={"fail": True}, max_attempts=1
        )
        _, _, first_result = await env["orchestration"].execute_task(
            user, org.id, company.id, task.id
        )
        assert not first_result.is_success()

        # Flip the policy to deny, then request a retry: the gate must refuse it.
        for agent in await env["agents_repo"].list_for_company(company.id):
            await env["permissions"].set_policy(
                user,
                org.id,
                company.id,
                principal_type="agent",
                principal_id=str(agent.id),
                resource_type="task",
                resource_id=None,
                action="execute",
                decision=PermissionDecision.DENY,
            )
        with pytest.raises(PermissionDenied):
            await env["orchestration"].execute_task(
                user, org.id, company.id, task.id, retry_failed=True
            )
        assert await _execution_count(env) == 1
