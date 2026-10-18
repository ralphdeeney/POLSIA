"""Phase 4 end-to-end: the native runtime inside the real orchestration stack.

Scenario (mandate §11): two isolated companies, one goal, a real plan
materialization, authorization, dispatch through :class:`NativeAgentRuntime`
with a deterministic scripted LLM that performs a controlled tool call, real
ToolGateway execution, verification, persistence, observation and a following
planning cycle. Negative paths: provider outage, forged completion claims and
cross-company isolation. SQLite + real services + real adapters; no fake
success shortcuts.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from polsia.adapters.agent_runtime import (
    ExecutionBudget,
    NativeAgentRuntime,
    ScriptedModelBehavior,
    ScriptedModelProvider,
    ScriptedResponse,
    ToolGateway,
    register_default_readonly_tools,
)
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
from polsia.adapters.runtime import DeterministicOrchestrator
from polsia.core.permissions.errors import PermissionDenied
from polsia.core.permissions.values import PermissionDecision
from polsia.core.planning.entities import Goal
from polsia.core.planning.values import GoalStatus, Priority
from polsia.core.runtime.ports import RuntimeStatus
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


def _final(payload: dict[str, Any]) -> ScriptedResponse:
    return ScriptedResponse(
        behavior=ScriptedModelBehavior.COMPLETE,
        content=json.dumps({"type": "final", **payload}),
    )


def _tool_echo(text: str) -> ScriptedResponse:
    return ScriptedResponse(
        behavior=ScriptedModelBehavior.TOOL_CALL,
        tool_name="echo",
        tool_arguments={"text": text},
    )


class ScriptedModel(ScriptedModelProvider):
    pass


def _build_native_runtime(script: list[ScriptedResponse], tool_root: Any) -> NativeAgentRuntime:
    gateway = ToolGateway()
    register_default_readonly_tools(gateway, whitelist_root=tool_root)
    return NativeAgentRuntime(
        model=ScriptedModelProvider(list(script)),
        tool_gateway=gateway,
        budget=ExecutionBudget(),
    )


@pytest.fixture
async def stack():
    """Real services over SQLite (as in the Phase 3 loop test)."""
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

            user = User.register(
                email="phase4@example.com", hashed_password="x", display_name="Phase4"
            )
            org = Organization.create(name="P4 Org", slug="p4-org", owner_user_id=user.id)
            company = Company.create(
                organization_id=org.id, name="Alpha Co", slug="alpha"
            ).transition_status(CompanyStatus.ACTIVE)
            other = Company.create(
                organization_id=org.id, name="Beta Co", slug="beta"
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
            registries = AgentService(agents, companies, memberships, bus)

            def build_stack(runtime: NativeAgentRuntime) -> dict[str, Any]:
                execution_service = ExecutionService(
                    tasks_repo,
                    executions_repo,
                    deps,
                    companies,
                    memberships,
                    bus,
                    authorization=authorization,
                )
                orchestration = OrchestrationService(
                    tasks=task_service,
                    executions=execution_service,
                    orchestrator=DeterministicOrchestrator(),
                    runtime=runtime,
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
                observer = CompanyStateObserver(
                    goals, kpis, tasks_repo, deps, agents, approval_repo
                )
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
                return {
                    "session": session,
                    "user": user,
                    "org": org,
                    "company": company,
                    "other": other,
                    "goals": goals,
                    "tasks": task_service,
                    "agents": registries,
                    "agents_repo": agents,
                    "permissions": permission_service,
                    "authorization": authorization,
                    "orchestration": orchestration,
                    "planning": planning,
                    "observer": observer,
                    "loop": loop,
                    "store": store,
                    "runtime": runtime,
                }

            yield build_stack
    finally:
        await engine.dispose()


async def _enable_agent(env_v: dict[str, Any], decision: PermissionDecision) -> None:
    user, org, company = env_v["user"], env_v["org"], env_v["company"]
    await env_v["agents"].register_agent(user, org.id, company.id, name="native-worker")
    for agent in await env_v["agents_repo"].list_for_company(company.id):
        await env_v["permissions"].set_policy(
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


async def _execution_count(env_v: dict[str, Any]) -> int:
    rows = (await env_v["session"].execute(select(TaskExecutionModel))).scalars().all()
    return len(list(rows))


async def _plan_two_tasks(env_v: dict[str, Any]) -> tuple[Any, Any]:
    user, org, company = env_v["user"], env_v["org"], env_v["company"]
    goal = Goal.create(
        company_id=company.id, title="Operate the company", priority=Priority.HIGH
    ).transition_status(GoalStatus.ACTIVE)
    await env_v["goals"].add(goal)
    plan = await env_v["planning"].plan_goal(
        user,
        org.id,
        company.id,
        goal.id,
        context={"objectives": ["read the operating note", "echo the status"]},
    )
    plan.validate()
    created = await env_v["planning"].materialize(
        user, org.id, company.id, plan, tasks_service=env_v["tasks"]
    )
    return created[0], created[1]


class TestNativeRuntimeEndToEnd:
    async def test_goal_plan_materialize_authorize_dispatch_observe_loop(
        self, stack: Any, tmp_path: Any
    ) -> None:
        (tmp_path / "note.txt").write_text("operating note", encoding="utf-8")
        runtime = _build_native_runtime(
            [
                _tool_echo("ping"),
                _final({"output": {"echo": "ping"}, "summary": "note read"}),
                _tool_echo("status"),
                _final({"output": {"echo": "status"}, "summary": "next cycle"}),
            ],
            tmp_path,
        )
        env_v = stack(runtime)
        await _enable_agent(env_v, PermissionDecision.ALLOW)
        first, second = await _plan_two_tasks(env_v)
        user, org, company = env_v["user"], env_v["org"], env_v["company"]

        # give the materialized tasks executable objectives
        await env_v["tasks"].update_task(
            user, org.id, company.id, first.id, input={"objective": "read the operating note"}
        )
        await env_v["tasks"].update_task(
            user, org.id, company.id, second.id, input={"objective": "echo the status"}
        )

        # authorize and dispatch through the native runtime
        authorized_task, agent = await env_v["authorization"].authorize_task(
            user, org.id, company.id, first.id
        )
        assert authorized_task.id == first.id and agent.is_selectable()

        observation_before = await env_v["observer"].observe(org.id, company.id)
        assert [t.id for t in observation_before.ready_tasks] == [first.id]

        task, execution, result = await env_v["orchestration"].execute_task(
            user,
            org.id,
            company.id,
            first.id,
            tool_permissions={"echo": "allow"},
            context={
                "agent_capability": "read_only_file_access",
                "company_context": "Alpha Co operates checkout.",
                "agent_role": "operations analyst",
            },
        )
        assert result.is_success()
        assert task.status is TaskStatus.COMPLETED
        assert execution.status.value == "completed"
        assert result.runtime == "native"
        assert len(result.tool_calls) == 1 and result.tool_calls[0].ok
        assert result.token_usage.total_tokens >= 0
        assert result.metadata["runtime_state"] == "completed"

        # event persisted durably for company A only
        events = await env_v["store"].list_for_company(org.id, company.id)
        assert any(event.type == "orchestration.executed" for event in events)
        other_events = await env_v["store"].list_for_company(org.id, env_v["other"].id)
        assert all(event.type != "orchestration.executed" for event in other_events)

        # observation now releases the second (dependent) task
        observation = await env_v["observer"].observe(org.id, company.id)
        assert [t.id for t in observation.ready_tasks] == [second.id]
        assert observation.digest

        # second cycle: the CEO dispatches the successor task
        task_second, execution_second, _ = await env_v["orchestration"].execute_task(
            user,
            org.id,
            company.id,
            second.id,
            tool_permissions={"echo": "allow"},
            context={
                "agent_capability": "read_only_file_access",
                "company_context": "Alpha Co operates checkout.",
                "agent_role": "operations analyst",
            },
        )
        assert task_second.status is TaskStatus.COMPLETED
        assert execution_second.status.value == "completed"

        # the planner/CEO loop observes previous outcomes and has no work left
        cycle = await env_v["loop"].run(user, org.id, company.id)
        assert cycle.stop_reason in {"no_eligible_work", "completed_all"}
        final_observation = await env_v["observer"].observe(org.id, company.id)
        assert final_observation.ready_tasks == []
        assert final_observation.tasks_total == 2

        # cross-company isolation: Beta Co sees nothing, acts on nothing
        with pytest.raises(NotFoundError):
            await env_v["authorization"].authorize_task(user, org.id, env_v["other"].id, first.id)
        beta_observation = await env_v["observer"].observe(org.id, env_v["other"].id)
        assert beta_observation.tasks_total == 0

    async def test_provider_outage_marks_task_failed(self, stack: Any, tmp_path: Any) -> None:
        (tmp_path / "note.txt").write_text("operating note", encoding="utf-8")
        runtime = _build_native_runtime(
            [ScriptedResponse(behavior=ScriptedModelBehavior.PROVIDER_FAILURE)], tmp_path
        )
        env_v = stack(runtime)
        await _enable_agent(env_v, PermissionDecision.ALLOW)
        user, org, company = env_v["user"], env_v["org"], env_v["company"]
        task = await env_v["tasks"].create_task(
            user,
            org.id,
            company.id,
            title="will hit an outage",
            input={"objective": "read the operating note"},
        )
        _, _, result = await env_v["orchestration"].execute_task(
            user,
            org.id,
            company.id,
            task.id,
            context={"agent_capability": "read_only_file_access"},
        )
        assert not result.is_success()
        refreshed = await env_v["tasks"].get_task(user, org.id, company.id, task.id)
        assert refreshed.status is TaskStatus.FAILED
        assert await _execution_count(env_v) == 1

    async def test_forged_completion_after_denied_tool_is_rejected(
        self, stack: Any, tmp_path: Any
    ) -> None:
        runtime = _build_native_runtime(
            [
                ScriptedResponse(
                    behavior=ScriptedModelBehavior.TOOL_CALL,
                    tool_name="read_file",
                    tool_arguments={"path": "does-not-exist.txt"},
                ),
                _final({"output": {"content": "I absolutely read the note"}, "summary": "forgery"}),
            ],
            tmp_path,
        )
        env_v = stack(runtime)
        await _enable_agent(env_v, PermissionDecision.ALLOW)
        user, org, company = env_v["user"], env_v["org"], env_v["company"]
        task = await env_v["tasks"].create_task(
            user,
            org.id,
            company.id,
            title="forgery must not pass",
            input={"objective": "read the operating note"},
        )
        task, execution, result = await env_v["orchestration"].execute_task(
            user,
            org.id,
            company.id,
            task.id,
            context={"agent_capability": "read_only_file_access"},
        )
        assert result.status is RuntimeStatus.FAILED
        assert "unverifiable" in (result.error or "")
        assert task.status is TaskStatus.FAILED
        assert execution.status.value == "failed"

    async def test_denied_dispatch_creates_no_execution(self, stack: Any, tmp_path: Any) -> None:
        runtime = _build_native_runtime([_final({"output": {"x": 1}, "summary": "ok"})], tmp_path)
        env_v = stack(runtime)
        await _enable_agent(env_v, PermissionDecision.DENY)
        user, org, company = env_v["user"], env_v["org"], env_v["company"]
        task = await env_v["tasks"].create_task(
            user, org.id, company.id, title="denied", input={"objective": "anything"}
        )
        with pytest.raises(PermissionDenied):
            await env_v["orchestration"].execute_task(
                user,
                org.id,
                company.id,
                task.id,
                context={"agent_capability": "read_only_file_access"},
            )
        assert await _execution_count(env_v) == 0
        refreshed = await env_v["tasks"].get_task(user, org.id, company.id, task.id)
        assert refreshed.status is TaskStatus.READY

    async def test_wrong_company_context_cannot_execute_croos_company(
        self, stack: Any, tmp_path: Any
    ) -> None:
        runtime = _build_native_runtime([_final({"output": {"x": 1}, "summary": "ok"})], tmp_path)
        env_v = stack(runtime)
        await _enable_agent(env_v, PermissionDecision.ALLOW)
        user, org, company = env_v["user"], env_v["org"], env_v["company"]
        task = await env_v["tasks"].create_task(
            user, org.id, company.id, title="alpha task", input={"objective": "work"}
        )
        with pytest.raises(NotFoundError):
            await env_v["orchestration"].execute_task(
                user,
                org.id,
                env_v["other"].id,
                task.id,
                context={"agent_capability": "read_only_file_access"},
            )
        assert await _execution_count(env_v) == 0

    async def test_token_budget_exhaustion_fails_cleanly(self, stack: Any, tmp_path: Any) -> None:
        """Token budget exhaustion is enforced before each model call."""
        gateway = ToolGateway()
        register_default_readonly_tools(gateway, whitelist_root=tmp_path)
        exhausted = NativeAgentRuntime(
            model=ScriptedModelProvider(
                [
                    ScriptedResponse(
                        behavior=ScriptedModelBehavior.TOOL_CALL,
                        tool_name="echo",
                        tool_arguments={"text": "big call"},
                        prompt_tokens=50,
                        completion_tokens=50,
                    ),
                    _final({"output": {"echo": "never"}, "summary": "unreachable"}),
                ]
            ),
            tool_gateway=gateway,
            budget=ExecutionBudget(max_model_tokens=10),
        )
        from polsia.core.runtime.ports import RuntimeRequest
        from polsia.core.shared.ids import AgentId, CompanyId, TaskId

        result = await exhausted.execute(
            RuntimeRequest(
                company_id=CompanyId.generate(),
                task_id=TaskId.generate(),
                agent_id=AgentId.generate(),
                input={"objective": "echo work"},
                context={"agent_capability": "read_only_file_access"},
            )
        )
        assert result.status is RuntimeStatus.FAILED
        assert "token budget" in (result.error or "")
        assert result.metadata["runtime_state"] == "failed"
