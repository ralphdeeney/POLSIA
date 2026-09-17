"""KPI endpoint tests (deterministic evaluation, company scoping)."""

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
        json={"name": "KPI Co", "slug": "kpi-co"},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return user, org, response.json()


def kpis_url(org_id: str, company_id: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/kpis"


async def create_kpi(
    client: AsyncClient, user: dict[str, Any], org_id: str, company_id: str, **extra: Any
) -> Any:
    payload: dict[str, Any] = {
        "name": extra.pop("name", "customers"),
        "direction": extra.pop("direction", "increase"),
        "target_value": extra.pop("target_value", 10),
        **extra,
    }
    response = await client.post(kpis_url(org_id, company_id), json=payload, headers=bearer(user))
    assert response.status_code == 201, response.text
    return response.json()


class TestKpiEvaluation:
    async def test_not_started_then_achieved(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "rachel@example.com")
        kpi = await create_kpi(client, user, org["id"], company["id"], target_value=10)
        assert kpi["status"] == "not_started"
        assert kpi["current_value"] is None
        url = kpis_url(org["id"], company["id"]) + f"/{kpi['id']}/measurements"
        response = await client.post(url, json={"current_value": 10}, headers=bearer(user))
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "achieved"
        assert body["current_value"] == 10

    async def test_on_track_and_at_risk_bands(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "steve@example.com")
        kpi = await create_kpi(client, user, org["id"], company["id"], target_value=100)
        url = kpis_url(org["id"], company["id"]) + f"/{kpi['id']}/measurements"
        response = await client.post(url, json={"current_value": 85}, headers=bearer(user))
        assert response.json()["status"] == "on_track"  # >= 80% band
        response = await client.post(url, json={"current_value": 40}, headers=bearer(user))
        assert response.json()["status"] == "at_risk"

    async def test_decrease_direction(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "tara@example.com")
        kpi = await create_kpi(
            client,
            user,
            org["id"],
            company["id"],
            name="bugs",
            direction="decrease",
            target_value=2,
        )
        url = kpis_url(org["id"], company["id"]) + f"/{kpi['id']}/measurements"
        response = await client.post(url, json={"current_value": 2}, headers=bearer(user))
        assert response.json()["status"] == "achieved"
        response = await client.post(url, json={"current_value": 20}, headers=bearer(user))
        assert response.json()["status"] == "at_risk"

    async def test_maintain_direction(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "uma@example.com")
        kpi = await create_kpi(
            client,
            user,
            org["id"],
            company["id"],
            name="uptime",
            direction="maintain",
            target_value=99.9,
        )
        url = kpis_url(org["id"], company["id"]) + f"/{kpi['id']}/measurements"
        response = await client.post(url, json={"current_value": 99.5}, headers=bearer(user))
        assert response.json()["status"] == "achieved"  # within 10% tolerance
        response = await client.post(url, json={"current_value": 50}, headers=bearer(user))
        assert response.json()["status"] == "at_risk"

    async def test_kpi_tied_to_goal(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "victor@example.com")
        goal_response = await client.post(
            f"/organizations/{org['id']}/companies/{company['id']}/goals",
            json={"title": "Growth"},
            headers=bearer(user),
        )
        goal = goal_response.json()
        kpi = await create_kpi(
            client, user, org["id"], company["id"], name="signups", goal_id=goal["id"]
        )
        assert kpi["goal_id"] == goal["id"]
        response = await client.get(
            kpis_url(org["id"], company["id"]) + f"?goal_id={goal['id']}", headers=bearer(user)
        )
        assert [k["id"] for k in response.json()] == [kpi["id"]]

    async def test_unknown_goal_404(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "wendy@example.com")
        response = await client.post(
            kpis_url(org["id"], company["id"]),
            json={
                "name": "x",
                "direction": "increase",
                "target_value": 1,
                "goal_id": "goal_01AAAAAAAAAAAAAAAAAAAAAAAA",
            },
            headers=bearer(user),
        )
        assert response.status_code == 404


class TestKpiIsolation:
    async def test_cross_tenant_access_denied(self, client: AsyncClient) -> None:
        user_a, org_a, company_a = await make_company(client, "xena@example.com")
        kpi = await create_kpi(client, user_a, org_a["id"], company_a["id"])
        intruder = await register(client, "yuri@example.com")
        url = kpis_url(org_a["id"], company_a["id"]) + f"/{kpi['id']}"
        response = await client.get(url, headers=bearer(intruder))
        assert response.status_code == 403
        response = await client.post(
            url + "/measurements", json={"current_value": 1}, headers=bearer(intruder)
        )
        assert response.status_code == 403

    async def test_unauthenticated_401(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "zoe@example.com")
        response = await client.get(kpis_url(org["id"], company["id"]))
        assert response.status_code == 401
