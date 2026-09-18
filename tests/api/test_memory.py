"""Memory endpoint tests (provider-neutral MemoryPort, tenant isolation)."""

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
        json={"name": "Memory Co", "slug": "memory-co"},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return user, org, response.json()


def memory_url(org_id: str, company_id: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/memory"


async def write_record(
    client: AsyncClient,
    user: dict[str, Any],
    org_id: str,
    company_id: str,
    content: str,
    **extra: Any,
) -> Any:
    payload: dict[str, Any] = {
        "scope": extra.pop("scope", "company"),
        "type": extra.pop("type", "observation"),
        "content": content,
        **extra,
    }
    response = await client.post(memory_url(org_id, company_id), json=payload, headers=bearer(user))
    assert response.status_code == 201, response.text
    return response.json()


class TestMemoryCrud:
    async def test_write_read_roundtrip(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "mema@example.com")
        record = await write_record(
            client, user, org["id"], company["id"], "Competitor X launched a new tier"
        )
        assert record["scope"] == "company"
        assert record["importance"] == 0.5
        response = await client.get(
            memory_url(org["id"], company["id"]) + f"/{record['id']}", headers=bearer(user)
        )
        assert response.status_code == 200
        assert response.json()["content"] == record["content"]

    async def test_list_and_scope_filter(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "memb@example.com")
        await write_record(client, user, org["id"], company["id"], "company note", scope="company")
        await write_record(client, user, org["id"], company["id"], "agent note", scope="agent")
        response = await client.get(memory_url(org["id"], company["id"]), headers=bearer(user))
        assert len(response.json()) == 2
        response = await client.get(
            memory_url(org["id"], company["id"]) + "?scope=agent", headers=bearer(user)
        )
        assert [r["scope"] for r in response.json()] == ["agent"]

    async def test_delete_then_gone(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "memc@example.com")
        record = await write_record(client, user, org["id"], company["id"], "temporary")
        response = await client.delete(
            memory_url(org["id"], company["id"]) + f"/{record['id']}", headers=bearer(user)
        )
        assert response.status_code == 204
        response = await client.get(
            memory_url(org["id"], company["id"]) + f"/{record['id']}", headers=bearer(user)
        )
        assert response.status_code == 404

    async def test_missing_record_404(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "memd@example.com")
        response = await client.get(
            memory_url(org["id"], company["id"]) + "/memr_01AAAAAAAAAAAAAAAAAAAAAAAA",
            headers=bearer(user),
        )
        assert response.status_code == 404


class TestMemorySearch:
    async def test_keyword_search(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "meme@example.com")
        await write_record(client, user, org["id"], company["id"], "pricing page needs update")
        await write_record(client, user, org["id"], company["id"], "hiring a designer")
        response = await client.get(
            memory_url(org["id"], company["id"]) + "/search?query=pricing", headers=bearer(user)
        )
        assert response.status_code == 200
        contents = [r["content"] for r in response.json()]
        assert contents == ["pricing page needs update"]

    async def test_search_is_case_insensitive(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "memf@example.com")
        await write_record(client, user, org["id"], company["id"], "Launch PRICING test")
        response = await client.get(
            memory_url(org["id"], company["id"]) + "/search?query=pricing", headers=bearer(user)
        )
        assert len(response.json()) == 1

    async def test_search_missing_query_422(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "memg@example.com")
        response = await client.get(
            memory_url(org["id"], company["id"]) + "/search", headers=bearer(user)
        )
        assert response.status_code == 422


class TestMemoryIsolation:
    async def test_cross_tenant_access_denied(self, client: AsyncClient) -> None:
        user_a, org_a, company_a = await make_company(client, "memh@example.com")
        record = await write_record(client, user_a, org_a["id"], company_a["id"], "secret note")
        intruder = await register(client, "memi@example.com")
        base = memory_url(org_a["id"], company_a["id"])
        assert (await client.get(base, headers=bearer(intruder))).status_code == 403
        assert (
            await client.get(base + f"/{record['id']}", headers=bearer(intruder))
        ).status_code == 403
        assert (
            await client.get(base + "/search?query=secret", headers=bearer(intruder))
        ).status_code == 403
        assert (
            await client.post(
                base,
                json={"scope": "company", "type": "note", "content": "x"},
                headers=bearer(intruder),
            )
        ).status_code == 403
        assert (
            await client.delete(base + f"/{record['id']}", headers=bearer(intruder))
        ).status_code == 403

    async def test_search_never_returns_other_tenant_records(self, client: AsyncClient) -> None:
        """Two orgs; A's search must never surface B's content (same keyword)."""
        user_a, org_a, company_a = await make_company(client, "memj@example.com")
        user_b, org_b, company_b = await make_company(client, "memk@example.com")
        await write_record(client, user_a, org_a["id"], company_a["id"], "shared keyword alpha")
        await write_record(client, user_b, org_b["id"], company_b["id"], "shared keyword beta")
        response = await client.get(
            memory_url(org_a["id"], company_a["id"]) + "/search?query=shared",
            headers=bearer(user_a),
        )
        contents = [r["content"] for r in response.json()]
        assert contents == ["shared keyword alpha"]

    async def test_unauthenticated_401(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "meml@example.com")
        response = await client.get(memory_url(org["id"], company["id"]))
        assert response.status_code == 401
