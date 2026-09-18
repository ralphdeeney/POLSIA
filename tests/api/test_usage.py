"""Usage API tests: records appear after orchestration executions; isolation."""

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
    SqlAlchemyUserRepository,
)
from polsia.adapters.persistence.task_repositories import (
    SqlAlchemyTaskDependencyRepository,
    SqlAlchemyTaskExecutionRepository,
    SqlAlchemyTaskRepository,
)
from polsia.adapters.persistence.usage_repositories import SqlAlchemyUsageRepository
from polsia.adapters.runtime import MockRuntime
from polsia.api.main import create_app
from polsia.core.tenancy.entities import User as DomainUser
from polsia.infra.db import create_all_metadata
from polsia.infra.settings import Settings
from polsia.services.orchestration import OrchestrationService
from polsia.services.planning import GoalService
from polsia.services.registries import AgentService
from polsia.services.tasks import ExecutionService, TaskService
from polsia.services.usage import UsageService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

PASSWORD = "super-secret-1"


@pytest.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        app = create_app(Settings(env="test", jwt_secret="test-secret"), session_factory=factory)
        transport = ASGITransport(app=app)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, factory
    finally:
        await engine.dispose()


async def register(client: AsyncClient, email: str) -> dict[str, Any]:
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(user: dict[str, Any]) -> dict[str, str]:
    token = user.get("tokens", {}).get("access_token") or user.get("access_token")
    return {"Authorization": f"Bearer {token}"}


async def run_one_execution(
    client: AsyncClient, factory: async_sessionmaker[AsyncSession], email: str
) -> None:
    """Register -> org -> company -> agent -> task -> one mock execution."""
    user = await register(client, email)
    headers = bearer(user)
    response = await client.post(
        "/organizations",
        json={"name": f"Org of {email}", "slug": email.split("@")[0]},
        headers=headers,
    )
    org = response.json()
    response = await client.post(
        f"/organizations/{org['id']}/companies",
        json={"name": "Usage Co", "slug": "usage-co"},
        headers=headers,
    )
    company = response.json()

    bus = InProcessEventBus()
    session = factory()
    stored = await SqlAlchemyUserRepository(session).get_by_email(email)
    assert stored is not None
    domain_user: DomainUser = stored
    companies = SqlAlchemyCompanyRepository(session)
    memberships = SqlAlchemyMembershipRepository(session)
    goals = SqlAlchemyGoalRepository(session)
    kpis = SqlAlchemyKpiRepository(session)
    tasks = SqlAlchemyTaskRepository(session)
    deps = SqlAlchemyTaskDependencyRepository(session)
    executions = SqlAlchemyTaskExecutionRepository(session)
    agents = SqlAlchemyAgentRepository(session)
    usage = SqlAlchemyUsageRepository(session)

    usage_service = UsageService(usage, companies, memberships, bus)
    orchestration = OrchestrationService(
        tasks=TaskService(tasks, deps, companies, memberships, bus),
        executions=ExecutionService(tasks, executions, deps, companies, memberships, bus),
        orchestrator=MockRuntime(),
        runtime=MockRuntime(),
        agents=agents,
        kpis=kpis,
        companies=companies,
        memberships=memberships,
        bus=bus,
        usage_service=usage_service,
    )
    _ = GoalService  # imported for parity with the capstone test
    await AgentService(agents, companies, memberships, bus).register_agent(
        domain_user, org["id"], company["id"], name="worker", model_reference="generic-llm@1.0.0"
    )
    task = await TaskService(tasks, deps, companies, memberships, bus).create_task(
        domain_user,
        org["id"],
        company["id"],
        title="measured work",
        input={"prompt_tokens": 100, "completion_tokens": 50, "tool_calls": ["web_search@1.0.0"]},
    )
    await orchestration.execute_task(domain_user, org["id"], company["id"], task.id)
    await session.commit()


def usage_url(org_id: str, company_id: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/usage"


class TestUsageApi:
    async def test_usage_recorded_after_execution(self, client: Any) -> None:
        http, _factory = client
        await run_one_execution(http, _factory, "usage-a@example.com")
        # find the org/company via the API
        user = await register(http, "usage-a2@example.com")  # fresh token for queries
        _ = user
        # log in again as the original user to get tokens
        response = await http.post(
            "/auth/login", json={"email": "usage-a@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200
        token_user = response.json()
        orgs = (await http.get("/organizations", headers=bearer(token_user))).json()
        org_id = orgs[0]["id"]
        companies = (
            await http.get(f"/organizations/{org_id}/companies", headers=bearer(token_user))
        ).json()
        company_id = companies[0]["id"]
        response = await http.get(usage_url(org_id, company_id), headers=bearer(token_user))
        assert response.status_code == 200, response.text
        records = response.json()
        assert len(records) == 1
        record = records[0]
        assert record["prompt_tokens"] == 100
        assert record["completion_tokens"] == 50
        assert record["runtime"] == "mock"
        assert record["model"] == "generic-llm@1.0.0"
        assert record["tool_calls"] == 1

    async def test_usage_isolation(self, client: Any) -> None:
        http, factory = client
        await run_one_execution(http, factory, "usage-b@example.com")
        intruder = await register(http, "usage-c@example.com")
        response = await http.post(
            "/auth/login", json={"email": "usage-b@example.com", "password": PASSWORD}
        )
        owner = response.json()
        orgs = (await http.get("/organizations", headers=bearer(owner))).json()
        org_id = orgs[0]["id"]
        companies = (
            await http.get(f"/organizations/{org_id}/companies", headers=bearer(owner))
        ).json()
        company_id = companies[0]["id"]
        # intruder (not a member) is denied
        response = await http.get(usage_url(org_id, company_id), headers=bearer(intruder))
        assert response.status_code == 403
        # owner sees exactly one record
        response = await http.get(usage_url(org_id, company_id), headers=bearer(owner))
        assert len(response.json()) == 1

    async def test_unauthenticated_401(self, client: Any) -> None:
        http, _factory = client
        user = await register(http, "usage-d@example.com")
        org_response = await http.post(
            "/organizations",
            json={"name": "Usage Org D", "slug": "usage-org-d"},
            headers=bearer(user),
        )
        assert org_response.status_code == 201
        orgs = [org_response.json()]
        response = await http.post(
            f"/organizations/{orgs[0]['id']}/companies",
            json={"name": "Usage Co D", "slug": "usage-co-d"},
            headers=bearer(user),
        )
        company_id = response.json()["id"]
        response = await http.get(usage_url(orgs[0]["id"], company_id))
        assert response.status_code == 401
