"""Task endpoint tests: dependency gating, executions, retry, isolation."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from polsia.api.main import create_app
from polsia.infra.db import create_all_metadata
from polsia.infra.settings import Settings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
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
            yield c
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
    return {"Authorization": f"Bearer {user['tokens']['access_token']}"}


async def make_company(
    client: AsyncClient, email: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    user = await register(client, email)
    response = await client.post(
        "/organizations",
        json={"name": f"Org of {email}", "slug": email.split("@")[0]},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    org = response.json()
    response = await client.post(
        f"/organizations/{org['id']}/companies",
        json={"name": "Task Co", "slug": "task-co"},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return user, org, response.json()


def tasks_url(org_id: str, company_id: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/tasks"


def executions_url(org_id: str, company_id: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/executions"


async def create_task(
    client: AsyncClient,
    user: dict[str, Any],
    org_id: str,
    company_id: str,
    title: str,
    **extra: Any,
) -> Any:
    response = await client.post(
        tasks_url(org_id, company_id), json={"title": title, **extra}, headers=bearer(user)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def run_to_completion(
    client: AsyncClient,
    user: dict[str, Any],
    org_id: str,
    company_id: str,
    task_id: str,
    output: dict[str, Any] | None = None,
) -> None:
    url = executions_url(org_id, company_id) + f"/tasks/{task_id}/start"
    response = await client.post(url, json={}, headers=bearer(user))
    assert response.status_code == 201, response.text
    execution_id = response.json()["id"]
    response = await client.post(
        executions_url(org_id, company_id) + f"/{execution_id}/complete",
        json={"output": output or {"done": True}},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text


class TestDependencyGating:
    async def test_no_dependencies_task_is_ready_immediately(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "adam@example.com")
        task = await create_task(client, user, org["id"], company["id"], "Standalone")
        assert task["status"] == "ready"

    async def test_dependent_task_stays_pending_until_prereq_completes(
        self, client: AsyncClient
    ) -> None:
        user, org, company = await make_company(client, "bella@example.com")
        task_a = await create_task(client, user, org["id"], company["id"], "A: research market")
        task_b = await create_task(
            client, user, org["id"], company["id"], "B: analyze", depends_on=[task_a["id"]]
        )
        assert task_b["status"] == "pending"
        # A incomplete -> B cannot run
        response = await client.post(
            tasks_url(org["id"], company["id"]) + f"/{task_b['id']}/status",
            json={"status": "ready"},
            headers=bearer(user),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "pending"  # still gated
        response = await client.post(
            executions_url(org["id"], company["id"]) + f"/tasks/{task_b['id']}/start",
            json={},
            headers=bearer(user),
        )
        assert response.status_code == 422  # pending cannot start
        # A completed -> B becomes ready
        await run_to_completion(client, user, org["id"], company["id"], task_a["id"])
        response = await client.get(
            tasks_url(org["id"], company["id"]) + f"/{task_b['id']}", headers=bearer(user)
        )
        assert response.json()["status"] == "ready"

    async def test_chain_of_four_tasks(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "carl@example.com")
        a = await create_task(client, user, org["id"], company["id"], "research market")
        b = await create_task(
            client, user, org["id"], company["id"], "analyze competitors", depends_on=[a["id"]]
        )
        c = await create_task(
            client, user, org["id"], company["id"], "define MVP", depends_on=[b["id"]]
        )
        d = await create_task(
            client, user, org["id"], company["id"], "build MVP", depends_on=[c["id"]]
        )
        for t in (b, c, d):
            assert t["status"] == "pending"
        assert a["status"] == "ready"  # no dependencies -> ready immediately
        for t in (a, b, c):
            await run_to_completion(client, user, org["id"], company["id"], t["id"])
        response = await client.get(
            tasks_url(org["id"], company["id"]) + f"/{d['id']}", headers=bearer(user)
        )
        assert response.json()["status"] == "ready"

    async def test_cycle_rejected(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "dana@example.com")
        a = await create_task(client, user, org["id"], company["id"], "A")
        b = await create_task(client, user, org["id"], company["id"], "B", depends_on=[a["id"]])
        response = await client.post(
            tasks_url(org["id"], company["id"]) + f"/{a['id']}/dependencies",
            json={"depends_on_task_id": b["id"]},
            headers=bearer(user),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "domain_rule_error"

    async def test_self_dependency_rejected(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "evan@example.com")
        a = await create_task(client, user, org["id"], company["id"], "A")
        response = await client.post(
            tasks_url(org["id"], company["id"]) + f"/{a['id']}/dependencies",
            json={"depends_on_task_id": a["id"]},
            headers=bearer(user),
        )
        assert response.status_code == 422

    async def test_unknown_dependency_404(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "fiona@example.com")
        response = await client.post(
            tasks_url(org["id"], company["id"]),
            json={"title": "A", "depends_on": ["tsk_01AAAAAAAAAAAAAAAAAAAAAAAA"]},
            headers=bearer(user),
        )
        assert response.status_code == 404


class TestExecutions:
    async def test_execution_history_append_only(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "gabe@example.com")
        task = await create_task(client, user, org["id"], company["id"], "Fix bug", max_attempts=5)
        # attempt 1 fails
        response = await client.post(
            executions_url(org["id"], company["id"]) + f"/tasks/{task['id']}/start",
            json={},
            headers=bearer(user),
        )
        ex1 = response.json()
        await client.post(
            executions_url(org["id"], company["id"]) + f"/{ex1['id']}/fail",
            json={"error": "boom"},
            headers=bearer(user),
        )
        # retry -> running (attempt 2) completes
        response = await client.post(
            tasks_url(org["id"], company["id"]) + f"/{task['id']}/retry", headers=bearer(user)
        )
        assert response.status_code == 200
        await run_to_completion(client, user, org["id"], company["id"], task["id"], {"fixed": True})
        # history: two executions, first failed, second completed
        response = await client.get(
            executions_url(org["id"], company["id"]) + f"/tasks/{task['id']}", headers=bearer(user)
        )
        executions = response.json()
        assert len(executions) == 2
        assert executions[0]["status"] == "failed"
        assert executions[0]["error"] == "boom"
        assert executions[1]["status"] == "completed"
        assert executions[1]["attempt_number"] == 2
        response = await client.get(
            tasks_url(org["id"], company["id"]) + f"/{task['id']}", headers=bearer(user)
        )
        assert response.json()["status"] == "completed"
        assert response.json()["attempts"] == 2

    async def test_retry_blocked_after_attempts_exhausted(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "hope@example.com")
        task = await create_task(client, user, org["id"], company["id"], "Fragile", max_attempts=1)
        response = await client.post(
            executions_url(org["id"], company["id"]) + f"/tasks/{task['id']}/start",
            json={},
            headers=bearer(user),
        )
        ex = response.json()
        await client.post(
            executions_url(org["id"], company["id"]) + f"/{ex['id']}/fail",
            json={"error": "boom"},
            headers=bearer(user),
        )
        response = await client.post(
            tasks_url(org["id"], company["id"]) + f"/{task['id']}/retry", headers=bearer(user)
        )
        assert response.status_code == 422
        assert response.json()["code"] == "domain_rule_error"


class TestTaskIsolation:
    async def test_cross_tenant_access_denied(self, client: AsyncClient) -> None:
        user_a, org_a, company_a = await make_company(client, "ivan@example.com")
        task = await create_task(client, user_a, org_a["id"], company_a["id"], "Secret")
        intruder = await register(client, "julia@example.com")
        response = await client.get(
            tasks_url(org_a["id"], company_a["id"]) + f"/{task['id']}", headers=bearer(intruder)
        )
        assert response.status_code == 403
        response = await client.get(
            executions_url(org_a["id"], company_a["id"]) + f"/tasks/{task['id']}",
            headers=bearer(intruder),
        )
        assert response.status_code == 403

    async def test_unauthenticated_401(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "karl2@example.com")
        response = await client.get(tasks_url(org["id"], company["id"]))
        assert response.status_code == 401
