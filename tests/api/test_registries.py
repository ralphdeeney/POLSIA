"""Registry endpoint tests (Phase 2, decision C2 scoping + versioning)."""

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
        json={"name": "Reg Co", "slug": "reg-co"},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return user, org, response.json()


def agents_url(org_id: str, company_id: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/agents"


class TestAgentRegistry:
    async def test_register_and_get(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "rega@example.com")
        response = await client.post(
            agents_url(org["id"], company["id"]),
            json={"name": "researcher", "type": "researcher", "capabilities": ["web.search"]},
            headers=bearer(user),
        )
        assert response.status_code == 201, response.text
        agent = response.json()
        assert agent["status"] == "active"
        assert agent["type"] == "researcher"
        response = await client.get(
            agents_url(org["id"], company["id"]) + f"/{agent['id']}", headers=bearer(user)
        )
        assert response.json()["name"] == "researcher"

    async def test_duplicate_name_conflict(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "regb@example.com")
        await client.post(
            agents_url(org["id"], company["id"]),
            json={"name": "worker"},
            headers=bearer(user),
        )
        response = await client.post(
            agents_url(org["id"], company["id"]),
            json={"name": "worker"},
            headers=bearer(user),
        )
        assert response.status_code == 409

    async def test_custom_free_form_type(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "regc@example.com")
        response = await client.post(
            agents_url(org["id"], company["id"]),
            json={"name": "custom-agent", "type": "my_weird_specialist"},
            headers=bearer(user),
        )
        assert response.status_code == 201
        assert response.json()["type"] == "my_weird_specialist"

    async def test_disable_enable(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "regd@example.com")
        response = await client.post(
            agents_url(org["id"], company["id"]), json={"name": "w"}, headers=bearer(user)
        )
        agent = response.json()
        url = agents_url(org["id"], company["id"]) + f"/{agent['id']}/status"
        response = await client.post(url, json={"status": "disabled"}, headers=bearer(user))
        assert response.json()["status"] == "disabled"
        response = await client.post(url, json={"status": "active"}, headers=bearer(user))
        assert response.json()["status"] == "active"

    async def test_isolation_between_companies(self, client: AsyncClient) -> None:
        user_a, org_a, company_a = await make_company(client, "rege@example.com")
        await client.post(
            agents_url(org_a["id"], company_a["id"]),
            json={"name": "secret-agent"},
            headers=bearer(user_a),
        )
        intruder = await register(client, "regf@example.com")
        response = await client.get(
            agents_url(org_a["id"], company_a["id"]), headers=bearer(intruder)
        )
        assert response.status_code == 403


class TestGlobalRegistries:
    async def test_skill_versioning(self, client: AsyncClient) -> None:
        user = await register(client, "regg@example.com")
        for version in ("1.0.0", "1.1.0"):
            response = await client.post(
                "/skills",
                json={"name": "market_research", "version": version},
                headers=bearer(user),
            )
            assert response.status_code == 201, response.text
        response = await client.get("/skills?name=market_research", headers=bearer(user))
        assert {s["version"] for s in response.json()} == {"1.0.0", "1.1.0"}

    async def test_duplicate_name_version_conflict(self, client: AsyncClient) -> None:
        user = await register(client, "regh@example.com")
        payload = {"name": "web_search", "version": "1.0.0"}
        await client.post("/tools", json=payload, headers=bearer(user))
        response = await client.post("/tools", json=payload, headers=bearer(user))
        assert response.status_code == 409

    async def test_same_name_different_version_ok(self, client: AsyncClient) -> None:
        user = await register(client, "regi@example.com")
        await client.post(
            "/tools", json={"name": "browser", "version": "1.0.0"}, headers=bearer(user)
        )
        response = await client.post(
            "/tools", json={"name": "browser", "version": "2.0.0"}, headers=bearer(user)
        )
        assert response.status_code == 201

    async def test_model_registry(self, client: AsyncClient) -> None:
        user = await register(client, "regj@example.com")
        response = await client.post(
            "/models",
            json={"name": "generic-llm", "version": "1.0.0", "provider": "generic"},
            headers=bearer(user),
        )
        assert response.status_code == 201, response.text
        model = response.json()
        assert model["status"] == "active"
        response = await client.get("/models?name=generic-llm", headers=bearer(user))
        assert len(response.json()) == 1

    async def test_invalid_semver_rejected(self, client: AsyncClient) -> None:
        user = await register(client, "regk@example.com")
        response = await client.post(
            "/skills",
            json={"name": "x", "version": "not-a-version"},
            headers=bearer(user),
        )
        assert response.status_code == 422

    async def test_unauthenticated_registry_write_401(self, client: AsyncClient) -> None:
        response = await client.post("/skills", json={"name": "x", "version": "1.0.0"})
        assert response.status_code == 401
