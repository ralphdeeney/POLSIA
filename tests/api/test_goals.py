"""Goal endpoint tests (state machine, company scoping, events-adjacent flows)."""

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
    """Register -> org -> company. Returns (user, org, company)."""
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
        json={"name": "Goal Co", "slug": "goal-co"},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return user, org, response.json()


def goals_url(org_id: str, company_id: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/goals"


async def create_goal(
    client: AsyncClient,
    user: dict[str, Any],
    org_id: str,
    company_id: str,
    title: str,
    **extra: Any,
) -> Any:
    response = await client.post(
        goals_url(org_id, company_id), json={"title": title, **extra}, headers=bearer(user)
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestGoalLifecycle:
    async def test_create_starts_in_draft(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "gina@example.com")
        goal = await create_goal(client, user, org["id"], company["id"], "Expand region")
        assert goal["status"] == "draft"
        assert goal["priority"] == "medium"
        assert goal["company_id"] == company["id"]
        assert goal["metadata"] == {}

    async def test_full_happy_path_transitions(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "hal@example.com")
        goal = await create_goal(client, user, org["id"], company["id"], "Launch")
        url = goals_url(org["id"], company["id"]) + f"/{goal['id']}/status"
        for status_value in ("active", "paused", "active", "completed"):
            response = await client.post(url, json={"status": status_value}, headers=bearer(user))
            assert response.status_code == 200, response.text
            assert response.json()["status"] == status_value

    async def test_invalid_transitions_rejected_422(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "iris@example.com")
        goal = await create_goal(client, user, org["id"], company["id"], "Jump")
        url = goals_url(org["id"], company["id"]) + f"/{goal['id']}/status"
        # draft -> paused / completed are invalid
        for status_value in ("paused", "completed"):
            response = await client.post(url, json={"status": status_value}, headers=bearer(user))
            assert response.status_code == 422
            assert response.json()["code"] == "validation_error"
        # terminal state: completed cannot move anywhere
        await client.post(url, json={"status": "active"}, headers=bearer(user))
        await client.post(url, json={"status": "completed"}, headers=bearer(user))
        response = await client.post(url, json={"status": "active"}, headers=bearer(user))
        assert response.status_code == 422

    async def test_update_fields(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "ivy@example.com")
        goal = await create_goal(client, user, org["id"], company["id"], "Original")
        response = await client.patch(
            goals_url(org["id"], company["id"]) + f"/{goal['id']}",
            json={"title": "Renamed", "priority": "critical", "metadata": {"note": "x"}},
            headers=bearer(user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["title"] == "Renamed"
        assert body["priority"] == "critical"
        assert body["metadata"] == {"note": "x"}

    async def test_subgoal_parent_linkage(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "jack@example.com")
        parent = await create_goal(client, user, org["id"], company["id"], "Parent")
        child = await create_goal(
            client, user, org["id"], company["id"], "Child", parent_goal_id=parent["id"]
        )
        assert child["parent_goal_id"] == parent["id"]
        response = await client.get(
            goals_url(org["id"], company["id"]) + f"/{parent['id']}/subgoals",
            headers=bearer(user),
        )
        assert response.status_code == 200
        assert [g["id"] for g in response.json()] == [child["id"]]

    async def test_unknown_parent_goal_404(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "kate@example.com")
        response = await client.post(
            goals_url(org["id"], company["id"]),
            json={"title": "Orphan", "parent_goal_id": "goal_01AAAAAAAAAAAAAAAAAAAAAAAA"},
            headers=bearer(user),
        )
        assert response.status_code == 404


class TestGoalIsolation:
    async def test_other_org_member_cannot_access(self, client: AsyncClient) -> None:
        user_a, org_a, company_a = await make_company(client, "larry@example.com")
        goal = await create_goal(client, user_a, org_a["id"], company_a["id"], "Secret goal")
        intruder = await register(client, "mallory@example.com")
        url = goals_url(org_a["id"], company_a["id"]) + f"/{goal['id']}"
        response = await client.get(url, headers=bearer(intruder))
        assert response.status_code == 403
        response = await client.patch(url, json={"title": "hijack"}, headers=bearer(intruder))
        assert response.status_code == 403
        response = await client.post(
            url + "/status", json={"status": "active"}, headers=bearer(intruder)
        )
        assert response.status_code == 403

    async def test_company_under_wrong_org_is_404(self, client: AsyncClient) -> None:
        user_a, org_a, company_a = await make_company(client, "nancy@example.com")
        await create_goal(client, user_a, org_a["id"], company_a["id"], "A goal")
        user_b, org_b, _ = await make_company(client, "oscar@example.com")
        response = await client.get(goals_url(org_b["id"], company_a["id"]), headers=bearer(user_b))
        assert response.status_code == 404  # company not in org B -> scope mismatch

    async def test_goal_ids_do_not_leak_across_companies(self, client: AsyncClient) -> None:
        user, org, company_a = await make_company(client, "pete@example.com")
        goal = await create_goal(client, user, org["id"], company_a["id"], "In A")
        # second company inside the same org
        response = await client.post(
            f"/organizations/{org['id']}/companies",
            json={"name": "Other Co", "slug": "other-co"},
            headers=bearer(user),
        )
        company_b = response.json()
        response = await client.get(
            goals_url(org["id"], company_b["id"]) + f"/{goal['id']}", headers=bearer(user)
        )
        assert response.status_code == 404

    async def test_unauthenticated_401(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "quinn@example.com")
        response = await client.get(goals_url(org["id"], company["id"]))
        assert response.status_code == 401
