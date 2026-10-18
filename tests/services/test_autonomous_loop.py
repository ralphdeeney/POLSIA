"""Bounded internal autonomous loop tests."""

from __future__ import annotations

import datetime as dt
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
from polsia.adapters.persistence.trigger_models import TriggerDeliveryModel
from polsia.adapters.persistence.trigger_repositories import SqlAlchemyEventTriggerPolicyRepository
from polsia.adapters.runtime import DeterministicOrchestrator, MockRuntime
from polsia.core.ceo.triggers import EventTriggerPolicy, SafetyLimits
from polsia.core.permissions.errors import ApprovalRequired, PermissionDenied
from polsia.core.permissions.values import PermissionDecision
from polsia.core.planning.values import Priority
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import EventEnvelope
from polsia.core.tasks.values import TaskStatus
from polsia.core.tenancy.entities import Company, Organization, OrgMembership, User
from polsia.core.tenancy.values import CompanyStatus, MembershipRole
from polsia.infra.db import create_all_metadata
from polsia.services.autonomous_loop import AutonomousLoop
from polsia.services.delegation import DelegationService
from polsia.services.event_triggers import EventTriggerDispatcher, trigger_cycle_id
from polsia.services.execution_authorization import ExecutionAuthorizationService
from polsia.services.observation import CompanyStateObserver
from polsia.services.orchestration import OrchestrationService
from polsia.services.permissions import ApprovalService, PermissionService
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
            companies = SqlAlchemyCompanyRepository(session)
            memberships = SqlAlchemyMembershipRepository(session)
            goals = SqlAlchemyGoalRepository(session)
            tasks_repo = SqlAlchemyTaskRepository(session)
            deps = SqlAlchemyTaskDependencyRepository(session)
            agents = SqlAlchemyAgentRepository(session)
            executions_repo = SqlAlchemyTaskExecutionRepository(session)
            user = User.register(email="loop@example.com", hashed_password="x", display_name="Loop")
            org = Organization.create(name="Loop Org", slug="loop-org", owner_user_id=user.id)
            company = Company.create(organization_id=org.id, name="Loop Co", slug="loop-co")
            company = company.transition_status(CompanyStatus.ACTIVE)
            await SqlAlchemyUserRepository(session).add(user)
            await SqlAlchemyOrganizationRepository(session).add(org)
            await companies.add(company)
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
                kpis=SqlAlchemyKpiRepository(session),
                companies=companies,
                memberships=memberships,
                bus=bus,
                permissions=permission_service,
                approvals=approval_service,
            )
            observer = CompanyStateObserver(
                goals,
                SqlAlchemyKpiRepository(session),
                tasks_repo,
                deps,
                agents,
                approval_repo,
            )
            delegation = DelegationService(task_service, agents, companies, memberships, bus)
            records = SqlAlchemyDecisionRecordRepository(session)
            loop = AutonomousLoop(
                observer=observer,
                prioritizer=PriorityEngine(),
                delegation=delegation,
                executions=execution_service,
                orchestration=orchestration,
                tasks=task_service,
                records=records,
                companies=companies,
                memberships=memberships,
                max_actions=2,
            )
            yield {
                "session": session,
                "factory": factory,
                "loop": loop,
                "user": user,
                "org": org,
                "company": company,
                "tasks": task_service,
                "agents": agent_service,
                "agents_repo": agents,
                "permissions": permission_service,
                "records": records,
                "runtime": orchestration._runtime,
            }
    finally:
        await engine.dispose()


async def _allow_agent_execution(env: dict[str, Any]) -> None:
    agents = await env["agents_repo"].list_for_company(env["company"].id)
    for agent in agents:
        await env["permissions"].set_policy(
            env["user"],
            env["org"].id,
            env["company"].id,
            principal_type="agent",
            principal_id=str(agent.id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )


class TestAutonomousLoop:
    async def test_executes_with_explicit_allow_and_records_digests(self, loop_env: Any) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        await env["agents"].register_agent(user, org.id, company.id, name="loop-worker")
        await _allow_agent_execution(env)
        first = await env["tasks"].create_task(user, org.id, company.id, title="first")
        second = await env["tasks"].create_task(user, org.id, company.id, title="second")
        result = await env["loop"].run(user, org.id, company.id)
        assert result.actions_taken == 2
        assert result.stop_reason == "action_budget_reached"
        assert len(result.decisions) == 2
        persisted = await env["records"].list_for_company(org.id, company.id)
        assert len(persisted) == 2
        assert all(record.observation_digest for record in persisted)
        assert all(record.outcome_context["status"] == "executed" for record in persisted)
        assert {str(record.decision.task_id) for record in persisted} == {
            str(first.id),
            str(second.id),
        }

    async def test_retryable_failed_task_retries_within_budget(self, loop_env: Any) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        await env["agents"].register_agent(user, org.id, company.id, name="retry-worker")
        await _allow_agent_execution(env)
        task = await env["tasks"].create_task(
            user, org.id, company.id, title="retry-once", input={"fail": True}, max_attempts=3
        )
        # First failed attempt is produced through the guarded orchestration path.
        first, first_execution, _ = await env["loop"]._orchestration.execute_task(
            user, org.id, company.id, task.id
        )
        assert first.status is TaskStatus.FAILED
        await env["tasks"].update_task(user, org.id, company.id, task.id, input={"fail": False})
        # The bounded recovery recommendation uses the failed attempt counter;
        # execute the retry via the same loop after replacing the runtime input.
        retried = await env["loop"].run(user, org.id, company.id, max_actions=1)
        assert retried.actions_taken == 1
        assert retried.decisions[0].decision.decision_type.value == "retry_task"
        refreshed = await env["tasks"].get_task(user, org.id, company.id, task.id)
        assert refreshed.status is TaskStatus.COMPLETED
        assert first_execution.status.value == "failed"

    async def test_loop_does_not_dispatch_blocked_or_exhausted_tasks(self, loop_env: Any) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        await env["agents"].register_agent(user, org.id, company.id, name="bounded-worker")
        await _allow_agent_execution(env)
        predecessor = await env["tasks"].create_task(
            user, org.id, company.id, title="unfinished", priority=Priority.CRITICAL
        )
        dependent = await env["tasks"].create_task(
            user,
            org.id,
            company.id,
            title="blocked high-priority",
            priority=Priority.CRITICAL,
            depends_on=[predecessor.id],
        )
        await env["loop"]._executions.start_execution(user, org.id, company.id, predecessor.id)
        exhausted = await env["tasks"].create_task(
            user, org.id, company.id, title="exhausted", input={"fail": True}, max_attempts=1
        )
        failed, _execution, _result = await env["loop"]._orchestration.execute_task(
            user, org.id, company.id, exhausted.id
        )
        assert failed.status is TaskStatus.FAILED
        result = await env["loop"].run(user, org.id, company.id)
        assert result.actions_taken == 0
        assert result.stop_reason == "wait_for_running_work"
        assert str(dependent.id) not in {str(item.decision.task_id) for item in result.decisions}
        assert str(exhausted.id) not in {str(item.decision.task_id) for item in result.decisions}
        predecessor_after = await env["tasks"].get_task(user, org.id, company.id, predecessor.id)
        blocked_after = await env["tasks"].get_task(user, org.id, company.id, dependent.id)
        assert predecessor_after.status is TaskStatus.RUNNING
        assert blocked_after.status is TaskStatus.PENDING

    async def test_stale_observation_cannot_exceed_iteration_budget(self, loop_env: Any) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        await env["agents"].register_agent(user, org.id, company.id, name="stale-worker")
        await _allow_agent_execution(env)
        task = await env["tasks"].create_task(user, org.id, company.id, title="stale-ready")
        stale_observation = await env["loop"]._observer.observe(org.id, company.id)
        await env["loop"]._executions.start_execution(user, org.id, company.id, task.id)

        async def observe_stale(_organization_id: Any, _company_id: Any) -> Any:
            return stale_observation

        env["loop"]._observe = observe_stale
        result = await env["loop"].run(user, org.id, company.id, max_actions=1)
        assert result.actions_taken == 0
        assert result.stop_reason == "iteration_budget_reached"
        assert len(result.decisions) == 2
        assert all(
            item.outcome_context["status"] == "skipped_status_changed" for item in result.decisions
        )

    async def test_denied_attempt_creates_no_execution(self, loop_env: Any) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        await env["agents"].register_agent(user, org.id, company.id, name="denied-worker")
        await env["tasks"].create_task(user, org.id, company.id, title="must not run")
        runtime_calls: list[object] = []
        runtime = env["runtime"]
        original_execute = runtime.execute

        async def spy_execute(request: object) -> object:
            runtime_calls.append(request)
            return await original_execute(request)  # type: ignore[arg-type]

        runtime.execute = spy_execute
        result = await env["loop"].run(user, org.id, company.id)
        assert result.actions_taken == 0
        assert result.stop_reason == "permission_denied"
        assert runtime_calls == []
        rows = (await env["session"].execute(select(TaskExecutionModel))).scalars().all()
        assert rows == []
        stored = await env["records"].list_for_company(org.id, company.id)
        assert len(stored) == 1
        assert stored[0].outcome_context["status"] == "permission_denied"

    async def test_approval_required_stops_without_execution(self, loop_env: Any) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        agent = await env["agents"].register_agent(user, org.id, company.id, name="approval-worker")
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(agent.id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.APPROVAL_REQUIRED,
        )
        await env["tasks"].create_task(user, org.id, company.id, title="approval-gated")
        result = await env["loop"].run(user, org.id, company.id)
        assert result.actions_taken == 0
        assert result.stop_reason == "approval_required"
        rows = (await env["session"].execute(select(TaskExecutionModel))).scalars().all()
        assert rows == []
        approvals = await SqlAlchemyApprovalRepository(env["session"]).list_pending(company.id)
        assert len(approvals) == 1

    async def test_triggered_cycle_executes_only_after_guard_and_explicit_allow(
        self, loop_env: Any
    ) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        agent = await env["agents"].register_agent(user, org.id, company.id, name="trigger-worker")
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(agent.id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        task = await env["tasks"].create_task(user, org.id, company.id, title="trigger-ready")
        policy = EventTriggerPolicy.create(
            organization_id=org.id,
            company_id=company.id,
            event_type="task.ready",
            enabled=True,
        )
        event = EventEnvelope(
            event_type="task.ready", organization_id=org.id, company_id=company.id
        )

        runner = env["loop"].run_triggered_cycle
        cycle_id = trigger_cycle_id(policy.id, event.id)
        with pytest.raises(ValidationError, match="stale or future"):
            await runner(
                user,
                policy,
                EventEnvelope(
                    event_type="task.ready",
                    organization_id=org.id,
                    company_id=company.id,
                    occurred_at=event.occurred_at - dt.timedelta(minutes=10),
                ),
                cycle_id,
            )
        rows = (await env["session"].execute(select(TaskExecutionModel))).scalars().all()
        assert rows == []

        result = await runner(user, policy, event, cycle_id)
        assert result["status"] == "executed"
        assert result["task_id"] == str(task.id)
        rows = (await env["session"].execute(select(TaskExecutionModel))).scalars().all()
        assert len(rows) == 1

    async def test_trigger_forged_payload_cannot_select_task_actor_or_runtime(
        self, loop_env: Any
    ) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        agent = await env["agents"].register_agent(user, org.id, company.id, name="forged-worker")
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(agent.id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        chosen = await env["tasks"].create_task(user, org.id, company.id, title="server-selected")
        forged = await env["tasks"].create_task(user, org.id, company.id, title="forged-target")
        runtime_calls: list[Any] = []
        runtime = env["runtime"]
        original_execute = runtime.execute

        async def spy_execute(request: Any) -> Any:
            runtime_calls.append(request)
            return await original_execute(request)

        runtime.execute = spy_execute
        policy = EventTriggerPolicy.create(
            organization_id=org.id,
            company_id=company.id,
            event_type="task.ready",
            enabled=True,
        )
        event = EventEnvelope(
            event_type="task.ready",
            organization_id=org.id,
            company_id=company.id,
            payload={
                "task_id": str(forged.id),
                "actor_id": "usr_forged",
                "runtime": "attacker-runtime",
                "model": "attacker-model",
            },
        )
        result = await env["loop"].run_triggered_cycle(
            user, policy, event, trigger_cycle_id(policy.id, event.id)
        )
        assert result["status"] == "executed"
        assert result["task_id"] == str(chosen.id)
        assert len(runtime_calls) == 1
        assert runtime_calls[0].task_id == chosen.id
        assert runtime_calls[0].agent_id == agent.id
        assert runtime_calls[0].runtime == "mock"
        assert runtime_calls[0].model == agent.model_reference
        assert runtime_calls[0].context == {}
        rows = (await env["session"].execute(select(TaskExecutionModel))).scalars().all()
        assert len(rows) == 1
        assert rows[0].task_id == str(chosen.id)
        assert rows[0].agent_id == str(agent.id)
        assert rows[0].runtime == "mock"

    @pytest.mark.parametrize(
        "limits",
        [
            SafetyLimits(max_actions=1),
            SafetyLimits(max_tasks=1),
            SafetyLimits(max_executions=1),
        ],
        ids=["action-cap", "task-cap", "execution-cap"],
    )
    async def test_trigger_executes_at_each_configured_single_action_cap(
        self, loop_env: Any, limits: SafetyLimits
    ) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        agent = await env["agents"].register_agent(user, org.id, company.id, name="cap-worker")
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(agent.id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        task = await env["tasks"].create_task(user, org.id, company.id, title="cap task")
        policy = EventTriggerPolicy.create(
            organization_id=org.id,
            company_id=company.id,
            event_type="task.ready",
            enabled=True,
            limits=limits,
        )
        event = EventEnvelope(
            event_type="task.ready", organization_id=org.id, company_id=company.id
        )
        await SqlAlchemyEventTriggerPolicyRepository(env["session"]).add(policy)
        await SqlAlchemyEventStore(env["session"]).append(event)
        await env["session"].commit()
        dispatcher = EventTriggerDispatcher(
            session_factory=env["factory"], run_cycle=env["loop"].run_triggered_cycle
        )
        assert await dispatcher.dispatch(event.id) == 1
        assert await dispatcher.dispatch(event.id) == 0
        assert await dispatcher.catch_up() == 0
        rows = (await env["session"].execute(select(TaskExecutionModel))).scalars().all()
        assert len(rows) == 1
        assert rows[0].task_id == str(task.id)
        assert len(await env["records"].list_for_company(org.id, company.id)) == 1
        deliveries = (await env["session"].execute(select(TriggerDeliveryModel))).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].status == "completed"

    async def test_trigger_retry_cap_rejects_before_retry_or_execution(self, loop_env: Any) -> None:
        """A task beyond its configured retry limit is not eligible trigger
        work: no retry, no execution, and no decision beyond the WAIT record.
        (Guard-level boundary rejection is covered by the SafetyGuard unit
        tests in test_event_triggers.py.)"""
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        agent = await env["agents"].register_agent(
            user, org.id, company.id, name="retry-cap-worker"
        )
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(agent.id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        task = await env["tasks"].create_task(
            user, org.id, company.id, title="retry cap", input={"fail": True}, max_attempts=4
        )
        await env["loop"]._orchestration.execute_task(user, org.id, company.id, task.id)
        await env["loop"]._executions.retry_task(user, org.id, company.id, task.id)
        await env["loop"]._orchestration.execute_task(user, org.id, company.id, task.id)
        before = await env["tasks"].get_task(user, org.id, company.id, task.id)
        assert before.status is TaskStatus.FAILED
        policy = EventTriggerPolicy.create(
            organization_id=org.id,
            company_id=company.id,
            event_type="task.failed",
            enabled=True,
            limits=SafetyLimits(max_retries_per_task=1),
        )
        event = EventEnvelope(
            event_type="task.failed", organization_id=org.id, company_id=company.id
        )
        result = await env["loop"].run_triggered_cycle(
            user, policy, event, trigger_cycle_id(policy.id, event.id)
        )
        assert result["status"] == "no_eligible_work"
        after = await env["tasks"].get_task(user, org.id, company.id, task.id)
        assert after.attempts == before.attempts
        rows = (
            (
                await env["session"].execute(
                    select(TaskExecutionModel).order_by(TaskExecutionModel.attempt_number)
                )
            )
            .scalars()
            .all()
        )
        assert [row.attempt_number for row in rows] == [1, 2]
        records = await env["records"].list_for_company(org.id, company.id)
        assert len(records) == 1
        assert records[0].outcome_context["reason"] == "no_eligible_work"

    async def test_configured_retry_limit_three_permits_third_retry_end_to_end(
        self, loop_env: Any
    ) -> None:
        """The trigger path enforces exactly the configured retry limit: a
        policy of 3 retries permits the third retry (two retries used), which
        a hidden default two-retry cap would have rejected."""
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        agent = await env["agents"].register_agent(
            user, org.id, company.id, name="retry-three-worker"
        )
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(agent.id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        task = await env["tasks"].create_task(
            user, org.id, company.id, title="retry three", input={"fail": True}, max_attempts=6
        )
        # burn three attempts (original run + two explicit retries), leaving
        # retries_used=2: within a configured limit of 3
        await env["loop"]._orchestration.execute_task(user, org.id, company.id, task.id)
        await env["loop"]._executions.retry_task(user, org.id, company.id, task.id)
        await env["loop"]._orchestration.execute_task(user, org.id, company.id, task.id)
        await env["loop"]._executions.retry_task(user, org.id, company.id, task.id)
        await env["loop"]._orchestration.execute_task(user, org.id, company.id, task.id)
        before = await env["tasks"].get_task(user, org.id, company.id, task.id)
        assert before.status is TaskStatus.FAILED
        assert before.attempts == 3

        policy = EventTriggerPolicy.create(
            organization_id=org.id,
            company_id=company.id,
            event_type="task.failed",
            enabled=True,
            limits=SafetyLimits(max_retries_per_task=3),
        )
        event = EventEnvelope(
            event_type="task.failed", organization_id=org.id, company_id=company.id
        )
        await env["loop"].run_triggered_cycle(
            user, policy, event, trigger_cycle_id(policy.id, event.id)
        )
        after = await env["tasks"].get_task(user, org.id, company.id, task.id)
        assert after.attempts == 4
        rows = (
            (
                await env["session"].execute(
                    select(TaskExecutionModel).order_by(TaskExecutionModel.attempt_number)
                )
            )
            .scalars()
            .all()
        )
        assert [row.attempt_number for row in rows] == [1, 2, 3, 4]

    async def test_task_max_attempts_bound_trigger_retry_independently(self, loop_env: Any) -> None:
        """A generous policy retry limit still cannot exceed the task's own
        max_attempts ceiling: an exhausted task is not eligible retry work."""
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        agent = await env["agents"].register_agent(
            user, org.id, company.id, name="max-attempt-worker"
        )
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(agent.id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        task = await env["tasks"].create_task(
            user, org.id, company.id, title="max attempts", input={"fail": True}, max_attempts=1
        )
        await env["loop"]._orchestration.execute_task(user, org.id, company.id, task.id)
        failed = await env["tasks"].get_task(user, org.id, company.id, task.id)
        assert failed.attempts == 1

        policy = EventTriggerPolicy.create(
            organization_id=org.id,
            company_id=company.id,
            event_type="task.failed",
            enabled=True,
            limits=SafetyLimits(max_retries_per_task=10),
        )
        event = EventEnvelope(
            event_type="task.failed", organization_id=org.id, company_id=company.id
        )
        result = await env["loop"].run_triggered_cycle(
            user, policy, event, trigger_cycle_id(policy.id, event.id)
        )
        assert result["status"] == "no_eligible_work"
        after = await env["tasks"].get_task(user, org.id, company.id, task.id)
        assert after.attempts == 1
        rows = (await env["session"].execute(select(TaskExecutionModel))).scalars().all()
        assert len(rows) == 1
        records = await env["records"].list_for_company(org.id, company.id)
        assert len(records) == 1
        assert records[0].outcome_context["reason"] == "no_eligible_work"

    async def test_stale_trigger_without_eligible_work_is_rejected_before_wait_record(
        self, loop_env: Any
    ) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        policy = EventTriggerPolicy.create(
            organization_id=org.id,
            company_id=company.id,
            event_type="task.ready",
            enabled=True,
        )
        event = EventEnvelope(
            event_type="task.ready",
            organization_id=org.id,
            company_id=company.id,
            occurred_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
        )

        with pytest.raises(ValidationError, match="stale or future"):
            await env["loop"].run_triggered_cycle(
                user, policy, event, trigger_cycle_id(policy.id, event.id)
            )
        assert await env["records"].list_for_company(org.id, company.id) == []

    async def test_triggered_cycle_deny_and_approval_never_call_runtime(
        self, loop_env: Any
    ) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        agent = await env["agents"].register_agent(user, org.id, company.id, name="trigger-gated")
        task = await env["tasks"].create_task(user, org.id, company.id, title="trigger-gated")
        runtime_calls: list[object] = []
        runtime = env["runtime"]
        original_execute = runtime.execute

        async def spy_execute(request: object) -> object:
            runtime_calls.append(request)
            return await original_execute(request)  # type: ignore[arg-type]

        runtime.execute = spy_execute
        policy = EventTriggerPolicy.create(
            organization_id=org.id,
            company_id=company.id,
            event_type="task.ready",
            enabled=True,
        )
        event = EventEnvelope(
            event_type="task.ready", organization_id=org.id, company_id=company.id
        )
        runner = env["loop"].run_triggered_cycle
        for decision, exception in (
            (PermissionDecision.DENY, PermissionDenied),
            (PermissionDecision.APPROVAL_REQUIRED, ApprovalRequired),
        ):
            await env["permissions"].set_policy(
                user,
                org.id,
                company.id,
                principal_type="agent",
                principal_id=str(agent.id),
                resource_type="task",
                resource_id=None,
                action="execute",
                decision=decision,
            )
            with pytest.raises(exception):
                await runner(user, policy, event, trigger_cycle_id(policy.id, event.id))
            rows = (await env["session"].execute(select(TaskExecutionModel))).scalars().all()
            assert rows == []
        assert runtime_calls == []
        assert task.status is TaskStatus.READY

    async def test_no_work_wait_record_and_budget_validation(self, loop_env: Any) -> None:
        env = loop_env
        user, org, company = env["user"], env["org"], env["company"]
        result = await env["loop"].run(user, org.id, company.id)
        assert result.actions_taken == 0
        assert result.stop_reason == "no_eligible_work"
        assert result.decisions[0].decision.decision_type.value == "wait"
        with pytest.raises(ValueError):
            await env["loop"].run(user, org.id, company.id, max_actions=3)
