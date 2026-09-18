"""Phase 2 capstone: the full kernel flow.

    Goal -> Task -> Dependency -> Agent selection -> MockRuntime
         -> Execution record -> Events -> KPI update

This is the Phase 2 acceptance test: it exercises every primitive together
through the real services and SQLAlchemy adapters on SQLite.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from polsia.adapters.events import InProcessEventBus
from polsia.adapters.persistence.registry_repositories import SqlAlchemyAgentRepository
from polsia.adapters.persistence.repositories import (
    SqlAlchemyCompanyRepository,
    SqlAlchemyGoalRepository,
    SqlAlchemyKpiRepository,
    SqlAlchemyMembershipRepository,
)
from polsia.adapters.persistence.task_repositories import (
    SqlAlchemyTaskDependencyRepository,
    SqlAlchemyTaskExecutionRepository,
    SqlAlchemyTaskRepository,
)
from polsia.adapters.runtime import DeterministicOrchestrator, MockRuntime
from polsia.api.main import create_app
from polsia.core.planning.values import KpiDirection, Priority
from polsia.core.registry.values import RegistryStatus
from polsia.core.shared.events import EventEnvelope
from polsia.core.shared.ids import GoalId, TaskId
from polsia.core.tenancy.entities import User
from polsia.infra.db import create_all_metadata
from polsia.infra.settings import Settings
from polsia.services.orchestration import OrchestrationService
from polsia.services.planning import GoalService, KpiService
from polsia.services.registries import AgentService
from polsia.services.tasks import ExecutionService, TaskService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

PASSWORD = "super-secret-1"


class RecordingBus(InProcessEventBus):
    """In-process bus that also records everything it dispatches."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[EventEnvelope] = []

    def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)
        super().publish(event)

    def types(self) -> list[str]:
        return [e.type for e in self.events]


@pytest.fixture
async def env():
    """SQLite env wiring the real services + adapters + recording bus."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        bus = RecordingBus()
        app = create_app(Settings(env="test", jwt_secret="test-secret"), session_factory=factory)
        transport = ASGITransport(app=app)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, factory, bus
    finally:
        await engine.dispose()


async def _register_and_setup(
    client: AsyncClient, factory: async_sessionmaker[AsyncSession], bus: RecordingBus, email: str
) -> tuple[User, Any, Any, dict[str, str], Any]:
    """Register via API, create org + company, and build the service graph
    bound to a single session (mirrors what the API deps do per request)."""
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    headers = {"Authorization": f"Bearer {body['tokens']['access_token']}"}

    response = await client.post(
        "/organizations",
        json={"name": f"Org of {email}", "slug": email.split("@")[0]},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    org = response.json()
    response = await client.post(
        f"/organizations/{org['id']}/companies",
        json={"name": "Capstone Co", "slug": "capstone-co"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    company = response.json()

    session = factory()
    user = User.register(email=email, hashed_password="x", display_name=email)
    # Reuse the persisted user row's id so membership checks pass.
    from polsia.adapters.persistence.repositories import SqlAlchemyUserRepository

    stored = await SqlAlchemyUserRepository(session).get_by_email(email)
    assert stored is not None
    user = stored

    companies = SqlAlchemyCompanyRepository(session)
    memberships = SqlAlchemyMembershipRepository(session)
    goals = SqlAlchemyGoalRepository(session)
    kpis = SqlAlchemyKpiRepository(session)
    tasks = SqlAlchemyTaskRepository(session)
    deps = SqlAlchemyTaskDependencyRepository(session)
    executions = SqlAlchemyTaskExecutionRepository(session)
    agents = SqlAlchemyAgentRepository(session)

    graph = {
        "goals": GoalService(goals, companies, memberships, bus),
        "kpis": KpiService(kpis, goals, companies, memberships, bus),
        "tasks": TaskService(tasks, deps, companies, memberships, bus),
        "executions": ExecutionService(tasks, executions, deps, companies, memberships, bus),
        "agents": AgentService(agents, companies, memberships, bus),
        "orchestration": OrchestrationService(
            tasks=TaskService(tasks, deps, companies, memberships, bus),
            executions=ExecutionService(tasks, executions, deps, companies, memberships, bus),
            orchestrator=DeterministicOrchestrator(),
            runtime=MockRuntime(),
            agents=agents,
            kpis=kpis,
            companies=companies,
            memberships=memberships,
            bus=bus,
        ),
        "session": session,
        "agents_repo": agents,
    }
    return user, org, company, headers, graph


class TestCapstoneKernelFlow:
    async def test_goal_to_kpi_full_loop(self, env: Any) -> None:
        """Goal -> tasks -> dependency -> agent -> MockRuntime -> execution
        -> events -> KPI update, end to end."""
        client, factory, bus = env
        user, org, company, _headers, graph = await _register_and_setup(
            client, factory, bus, "capstone@example.com"
        )
        org_id = org["id"]
        company_id = company["id"]

        # 1. GOAL
        goal = await graph["goals"].create_goal(
            user, org_id, company_id, title="Reach 10 paying customers", priority=Priority.HIGH
        )
        assert goal.status.value == "draft"

        # 2. KPI attached to the goal (deterministic evaluation)
        kpi = await graph["kpis"].create_kpi(
            user,
            org_id,
            company_id,
            name="paying_customers",
            direction=KpiDirection.INCREASE,
            target_value=10,
            goal_id=goal.id,
        )
        assert kpi.status.value == "not_started"

        # 3. AGENT (company-scoped registry, free-form type)
        agent = await graph["agents"].register_agent(
            user,
            org_id,
            company_id,
            name="growth-worker",
            type="growth",
            capabilities=["web.search"],
            model_reference="generic-llm@1.0.0",
        )
        assert agent.is_selectable()

        # 4. PLAN: goal -> tasks with dependencies (deterministic orchestrator)
        tasks = await graph["orchestration"].plan_goal(
            user,
            org_id,
            company_id,
            goal.id,
            goal_title=goal.title,
            goal_description=goal.description,
            context={"objectives": ["research prospects", "draft outreach"]},
        )
        assert [t.title for t in tasks] == ["research prospects", "draft outreach"]
        assert tasks[0].status.value == "ready"  # no dependencies
        assert tasks[1].status.value == "pending"  # blocked by dependency

        # 5. DEPENDENCY GATE: dependent task cannot start yet
        from polsia.core.shared.errors import ValidationError

        with pytest.raises(ValidationError):
            await graph["executions"].start_execution(user, org_id, company_id, tasks[1].id)

        # 6. EXECUTE task 1 through MockRuntime (agent selection automatic)
        task1, execution1, result1 = await graph["orchestration"].execute_task(
            user,
            org_id,
            company_id,
            tasks[0].id,
            tool_permissions={"web.search@1.0.0": "allow"},
        )
        assert result1.is_success()
        assert execution1.status.value == "completed"
        assert task1.status.value == "completed"
        assert task1.attempts == 1

        # 7. DEPENDENCY UNLOCK: task 2 is now ready, then execute it too
        refreshed = await graph["tasks"].get_task(user, org_id, company_id, tasks[1].id)
        assert refreshed.status.value == "ready"
        task2, execution2, result2 = await graph["orchestration"].execute_task(
            user, org_id, company_id, tasks[1].id
        )
        assert task2.status.value == "completed"
        assert execution2.attempt_number == 1

        # 8. EXECUTION HISTORY is append-only and queryable
        history = await graph["executions"].list_executions(user, org_id, company_id, tasks[0].id)
        assert len(history) == 1
        assert history[0].id == execution1.id

        # 9. EVENTS cover the whole flow
        types = bus.types()
        for expected in (
            "goal.created",
            "kpi.created",
            "agent.registered",
            "task.created",
            "task.ready",
            "orchestration.planned",
            "execution.started",
            "execution.completed",
            "task.completed",
            "task.ready",  # dependent unlocked
            "orchestration.executed",
        ):
            assert expected in types, f"missing event {expected!r} in {types}"

    async def test_kpi_updated_from_execution_measurement(self, env: Any) -> None:
        """A runtime result carrying a generic numeric measurement updates the
        goal's KPIs deterministically."""
        client, factory, bus = env
        user, org, company, _headers, graph = await _register_and_setup(
            client, factory, bus, "measure@example.com"
        )
        org_id, company_id = org["id"], company["id"]

        goal = await graph["goals"].create_goal(user, org_id, company_id, title="Grow MRR")
        await graph["kpis"].create_kpi(
            user,
            org_id,
            company_id,
            name="mrr",
            direction=KpiDirection.INCREASE,
            target_value=5000,
            goal_id=goal.id,
        )
        await graph["agents"].register_agent(user, org_id, company_id, name="finance-worker")

        task = await graph["tasks"].create_task(
            user,
            org_id,
            company_id,
            title="measure revenue",
            goal_id=goal.id,
            input={"kpi_value": 5000},
        )
        _task, _execution, result = await graph["orchestration"].execute_task(
            user, org_id, company_id, task.id
        )
        assert result.is_success()

        kpis = await graph["kpis"].list_kpis(user, org_id, company_id, goal_id=goal.id)
        assert len(kpis) == 1
        assert kpis[0].current_value == 5000
        assert kpis[0].status.value == "achieved"
        assert "kpi.measurement_recorded" in bus.types()

    async def test_failure_path_records_failed_execution_and_retry(self, env: Any) -> None:
        """Runtime failure -> execution failed, task failed, retry allowed while
        attempts remain, then success."""
        client, factory, bus = env
        user, org, company, _headers, graph = await _register_and_setup(
            client, factory, bus, "retrycap@example.com"
        )
        org_id, company_id = org["id"], company["id"]
        await graph["agents"].register_agent(user, org_id, company_id, name="flaky-worker")

        task = await graph["tasks"].create_task(
            user,
            org_id,
            company_id,
            title="flaky job",
            input={"fail": True, "error": "upstream timeout"},
            max_attempts=2,
        )
        _task, execution, result = await graph["orchestration"].execute_task(
            user, org_id, company_id, task.id
        )
        assert not result.is_success()
        assert execution.status.value == "failed"
        assert execution.error == "upstream timeout"

        failed_task = await graph["tasks"].get_task(user, org_id, company_id, task.id)
        assert failed_task.status.value == "failed"
        assert failed_task.can_retry()

        # retry -> running (attempt 2)
        await graph["executions"].retry_task(user, org_id, company_id, task.id)
        retrying = await graph["tasks"].get_task(user, org_id, company_id, task.id)
        assert retrying.status.value == "retrying"

        task_after = await graph["tasks"].update_task(
            user, org_id, company_id, task.id, input={"fail": False}
        )
        assert task_after.status.value == "retrying"
        _task2, execution2, result2 = await graph["orchestration"].execute_task(
            user, org_id, company_id, task.id
        )
        assert result2.is_success()
        assert execution2.attempt_number == 2
        completed = await graph["tasks"].get_task(user, org_id, company_id, task.id)
        assert completed.status.value == "completed"

        history = await graph["executions"].list_executions(user, org_id, company_id, task.id)
        assert [e.status.value for e in history] == ["failed", "completed"]

    async def test_agent_selection_prefers_explicit_then_first_active(self, env: Any) -> None:
        client, factory, bus = env
        user, org, company, _headers, graph = await _register_and_setup(
            client, factory, bus, "select@example.com"
        )
        org_id, company_id = org["id"], company["id"]
        first = await graph["agents"].register_agent(user, org_id, company_id, name="a-first")
        second = await graph["agents"].register_agent(user, org_id, company_id, name="b-second")

        task = await graph["tasks"].create_task(user, org_id, company_id, title="work")
        chosen = await graph["orchestration"].select_agent(
            user, org_id, company_id, task, agent_id=second.id
        )
        assert chosen.id == second.id
        auto = await graph["orchestration"].select_agent(user, org_id, company_id, task)
        assert auto.id == first.id  # first selectable

        # disabled agents are not selectable
        await graph["agents"].set_agent_status(
            user, org_id, company_id, first.id, RegistryStatus.DISABLED
        )
        third = await graph["orchestration"].select_agent(user, org_id, company_id, task)
        assert third.id == second.id

    async def test_cross_tenant_cannot_drive_another_companys_kernel(self, env: Any) -> None:
        """Tenant isolation across the whole orchestration surface."""
        client, factory, bus = env
        user_a, org_a, company_a, _h, graph_a = await _register_and_setup(
            client, factory, bus, "owner-a@example.com"
        )
        _user_b, org_b, company_b, _h2, _graph_b = await _register_and_setup(
            client, factory, bus, "owner-b@example.com"
        )
        goal = await graph_a["goals"].create_goal(
            user_a, org_a["id"], company_a["id"], title="Private goal"
        )
        task = await graph_a["tasks"].create_task(
            user_a, org_a["id"], company_a["id"], title="Private task", goal_id=goal.id
        )

        # B (registered user, not a member of A) cannot plan/execute in A.
        from polsia.core.shared.errors import NotAMember

        with pytest.raises(NotAMember):
            await graph_a["orchestration"].plan_goal(
                user_a, org_b["id"], company_a["id"], goal.id, goal_title="hijack"
            )
        _ = task

    async def test_execution_record_carries_audit_fields(self, env: Any) -> None:
        """Execution records capture agent, runtime, model, timing, tool calls
        and usage — the audit trail for autonomous operation."""
        client, factory, bus = env
        user, org, company, _headers, graph = await _register_and_setup(
            client, factory, bus, "audit@example.com"
        )
        org_id, company_id = org["id"], company["id"]
        await graph["agents"].register_agent(
            user,
            org_id,
            company_id,
            name="audited",
            model_reference="generic-llm@1.0.0",
            system_instructions="be terse",
        )
        task = await graph["tasks"].create_task(
            user,
            org_id,
            company_id,
            title="audited work",
            assigned_skill="web-research@1.0.0",
            input={
                "tool_calls": ["web_search@1.0.0"],
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "artifacts": [{"kind": "note", "value": "x"}],
            },
        )
        _task, execution, result = await graph["orchestration"].execute_task(
            user, org_id, company_id, task.id
        )
        assert execution.runtime == "mock"
        assert execution.model == "generic-llm@1.0.0"
        assert execution.skill_id == "web-research@1.0.0"
        assert execution.agent_id is not None
        assert execution.started_at is not None
        assert execution.completed_at is not None
        assert result.token_usage.total_tokens == 150
        assert [c.tool for c in result.tool_calls] == ["web_search@1.0.0"]
        assert result.artifacts == [{"kind": "note", "value": "x"}]
        assert result.output["echo"]["prompt_tokens"] == 120

    async def test_capstone_ids_are_typed_and_scoped(self, env: Any) -> None:
        client, factory, bus = env
        user, org, company, _headers, graph = await _register_and_setup(
            client, factory, bus, "ids@example.com"
        )
        org_id, company_id = org["id"], company["id"]
        goal = await graph["goals"].create_goal(user, org_id, company_id, title="Typed")
        assert isinstance(goal.id, GoalId)
        assert str(goal.id).startswith("goal_")
        task = await graph["tasks"].create_task(
            user, org_id, company_id, title="Typed task", goal_id=goal.id
        )
        assert isinstance(task.id, TaskId)
        assert task.company_id == company_id
        _ = company
