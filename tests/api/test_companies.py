"""Company endpoint tests (nested routes, JSON fields, status machine)."""

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


async def make_org(client: AsyncClient, email: str) -> tuple[dict[str, Any], Any]:
    user = await register(client, email)
    response = await client.post(
        "/organizations",
        json={"name": f"Org of {email}", "slug": email.split("@")[0]},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return user, response.json()


async def create_company(
    client: AsyncClient,
    user: dict[str, Any],
    org_id: str,
    name: str = "Coffee Shop",
    slug: str = "coffee-shop",
    **extra: Any,
) -> Any:
    payload: dict[str, Any] = {"name": name, "slug": slug, **extra}
    response = await client.post(
        f"/organizations/{org_id}/companies", json=payload, headers=bearer(user)
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestCreateCompany:
    async def test_create_starts_in_draft(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "alice@example.com")
        company = await create_company(
            client,
            user,
            org["id"],
            identity={"industry": "food"},
            mission="Great coffee",
        )

        assert company["organization_id"] == org["id"]
        assert company["status"] == "draft"
        assert company["identity"] == {"industry": "food"}
        assert company["mission"] == "Great coffee"
        assert company["strategy"] == {}
        assert company["configuration"] == {}

    async def test_requires_membership(self, client: AsyncClient) -> None:
        owner, org = await make_org(client, "bob@example.com")
        _ = owner
        outsider = await register(client, "eve@example.com")
        response = await client.post(
            f"/organizations/{org['id']}/companies",
            json={"name": "Sneaky", "slug": "sneaky"},
            headers=bearer(outsider),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "not_a_member"

    async def test_member_can_create(self, client: AsyncClient) -> None:
        owner, org = await make_org(client, "carol@example.com")
        member = await register(client, "dave@example.com")
        await client.post(
            f"/organizations/{org['id']}/members",
            json={"user_id": member["user"]["id"], "role": "member"},
            headers=bearer(owner),
        )
        company = await create_company(client, member, org["id"], "Member Co", "member-co")
        assert company["slug"] == "member-co"

    async def test_duplicate_slug_conflicts(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "erin@example.com")
        await create_company(client, user, org["id"], "One", "same")
        response = await client.post(
            f"/organizations/{org['id']}/companies",
            json={"name": "Two", "slug": "same"},
            headers=bearer(user),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "duplicate_slug"


class TestGetAndListCompanies:
    async def test_get_company(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "frank@example.com")
        company = await create_company(client, user, org["id"])
        response = await client.get(
            f"/organizations/{org['id']}/companies/{company['id']}",
            headers=bearer(user),
        )
        assert response.status_code == 200
        assert response.json()["id"] == company["id"]

    async def test_list_companies(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "gina@example.com")
        await create_company(client, user, org["id"], "Alpha", "alpha")
        await create_company(client, user, org["id"], "Beta", "beta")
        response = await client.get(f"/organizations/{org['id']}/companies", headers=bearer(user))
        assert response.status_code == 200
        assert {c["slug"] for c in response.json()} == {"alpha", "beta"}

    async def test_unknown_company_not_found(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "henry@example.com")
        response = await client.get(
            f"/organizations/{org['id']}/companies/cmp_01AAAAAAAAAAAAAAAAAAAAAAAA",
            headers=bearer(user),
        )
        assert response.status_code == 404
        assert response.json()["code"] == "company_not_found"


class TestUpdateCompany:
    async def test_patch_all_fields(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "iris@example.com")
        company = await create_company(client, user, org["id"])

        response = await client.patch(
            f"/organizations/{org['id']}/companies/{company['id']}",
            json={
                "name": "Cafe Prime",
                "identity": {"cuisine": "coffee", "founded": 2026},
                "mission": "Best coffee in town",
                "strategy": {"markets": ["EU"]},
                "configuration": {"hours": "08:00-18:00"},
            },
            headers=bearer(user),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Cafe Prime"
        assert body["identity"] == {"cuisine": "coffee", "founded": 2026}
        assert body["mission"] == "Best coffee in town"
        assert body["strategy"] == {"markets": ["EU"]}
        assert body["configuration"] == {"hours": "08:00-18:00"}

    async def test_patch_is_partial(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "jack@example.com")
        company = await create_company(client, user, org["id"], mission="Original")
        response = await client.patch(
            f"/organizations/{org['id']}/companies/{company['id']}",
            json={"mission": "Updated"},
            headers=bearer(user),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["mission"] == "Updated"
        assert body["name"] == "Coffee Shop"  # untouched


class TestStatusTransitions:
    async def test_draft_active_paused_active(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "kate@example.com")
        company = await create_company(client, user, org["id"])
        url = f"/organizations/{org['id']}/companies/{company['id']}/status"

        for expected in ("active", "paused", "active"):
            response = await client.post(url, json={"status": expected}, headers=bearer(user))
            assert response.status_code == 200, response.text
            assert response.json()["status"] == expected

    async def test_draft_to_paused_is_invalid(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "leo@example.com")
        company = await create_company(client, user, org["id"])
        response = await client.post(
            f"/organizations/{org['id']}/companies/{company['id']}/status",
            json={"status": "paused"},
            headers=bearer(user),
        )
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "invalid_status_transition"
        assert body["status"] == 422
        assert "draft" in body["detail"] and "paused" in body["detail"]

    async def test_unknown_status_unprocessable(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "mia@example.com")
        company = await create_company(client, user, org["id"])
        response = await client.post(
            f"/organizations/{org['id']}/companies/{company['id']}/status",
            json={"status": "quantum"},
            headers=bearer(user),
        )
        assert response.status_code == 422
