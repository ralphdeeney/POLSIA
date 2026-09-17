"""Organization endpoint tests (owner membership, roles, member listing)."""

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


async def register(client: AsyncClient, email: str, display_name: str = "User") -> dict[str, Any]:
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": display_name},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(user: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['tokens']['access_token']}"}


async def create_org(client: AsyncClient, user: dict[str, Any], slug: str) -> Any:
    response = await client.post(
        "/organizations",
        json={"name": slug.title(), "slug": slug},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestCreateOrganization:
    async def test_create_creates_owner_membership(self, client: AsyncClient) -> None:
        user = await register(client, "alice@example.com", "Alice")
        org = await create_org(client, user, "acme")

        assert org["name"] == "Acme"
        assert org["slug"] == "acme"
        assert org["status"] == "active"
        assert org["owner_user_id"] == user["user"]["id"]

        members = await client.get(f"/organizations/{org['id']}/members", headers=bearer(user))
        assert members.status_code == 200
        listing = members.json()
        assert len(listing) == 1
        assert listing[0]["user_id"] == user["user"]["id"]
        assert listing[0]["role"] == "owner"

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.post("/organizations", json={"name": "Anon", "slug": "anon"})
        assert response.status_code == 401

    async def test_duplicate_slug_conflicts(self, client: AsyncClient) -> None:
        user = await register(client, "bob@example.com", "Bob")
        await create_org(client, user, "dupe")
        response = await client.post(
            "/organizations",
            json={"name": "Dupe Two", "slug": "dupe"},
            headers=bearer(user),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "duplicate_slug"

    async def test_invalid_slug_unprocessable(self, client: AsyncClient) -> None:
        user = await register(client, "carol@example.com", "Carol")
        response = await client.post(
            "/organizations",
            json={"name": "Bad", "slug": "Bad Slug!"},
            headers=bearer(user),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_slug"


class TestListOrganizations:
    async def test_list_mine(self, client: AsyncClient) -> None:
        user = await register(client, "dave@example.com", "Dave")
        other = await register(client, "eve@example.com", "Eve")
        own = await create_org(client, user, "daves")
        joined = await create_org(client, other, "eves")
        # Dave joins Eve's org (Eve is owner -> can add).
        add = await client.post(
            f"/organizations/{joined['id']}/members",
            json={"user_id": user["user"]["id"], "role": "member"},
            headers=bearer(other),
        )
        assert add.status_code == 201

        response = await client.get("/organizations", headers=bearer(user))
        assert response.status_code == 200
        ids = {o["id"] for o in response.json()}
        assert ids == {own["id"], joined["id"]}


class TestGetOrganization:
    async def test_owner_can_get(self, client: AsyncClient) -> None:
        user = await register(client, "frank@example.com", "Frank")
        org = await create_org(client, user, "franks")
        response = await client.get(f"/organizations/{org['id']}", headers=bearer(user))
        assert response.status_code == 200
        assert response.json()["id"] == org["id"]

    async def test_unknown_org_not_found(self, client: AsyncClient) -> None:
        user = await register(client, "gina@example.com", "Gina")
        response = await client.get(
            "/organizations/org_01AAAAAAAAAAAAAAAAAAAAAAAA", headers=bearer(user)
        )
        assert response.status_code == 404
        assert response.json()["code"] == "organization_not_found"


class TestRenameOrganization:
    async def test_owner_renames(self, client: AsyncClient) -> None:
        user = await register(client, "henry@example.com", "Henry")
        org = await create_org(client, user, "henrys")
        response = await client.patch(
            f"/organizations/{org['id']}",
            json={"name": "Renamed"},
            headers=bearer(user),
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"

    async def test_admin_can_rename(self, client: AsyncClient) -> None:
        owner = await register(client, "iris@example.com", "Iris")
        admin = await register(client, "admin@example.com", "Admin")
        org = await create_org(client, owner, "iris-org")
        add = await client.post(
            f"/organizations/{org['id']}/members",
            json={"user_id": admin["user"]["id"], "role": "admin"},
            headers=bearer(owner),
        )
        assert add.status_code == 201

        response = await client.patch(
            f"/organizations/{org['id']}",
            json={"name": "Admin Renamed"},
            headers=bearer(admin),
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Admin Renamed"

    async def test_member_cannot_rename(self, client: AsyncClient) -> None:
        owner = await register(client, "jack@example.com", "Jack")
        member = await register(client, "member@example.com", "Member")
        org = await create_org(client, owner, "jacks")
        await client.post(
            f"/organizations/{org['id']}/members",
            json={"user_id": member["user"]["id"], "role": "member"},
            headers=bearer(owner),
        )
        response = await client.patch(
            f"/organizations/{org['id']}",
            json={"name": "Nope"},
            headers=bearer(member),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "insufficient_role"


class TestMembers:
    async def test_owner_adds_member(self, client: AsyncClient) -> None:
        owner = await register(client, "kate@example.com", "Kate")
        member = await register(client, "mia@example.com", "Mia")
        org = await create_org(client, owner, "kates")

        response = await client.post(
            f"/organizations/{org['id']}/members",
            json={"user_id": member["user"]["id"], "role": "member"},
            headers=bearer(owner),
        )
        assert response.status_code == 201
        assert response.json()["role"] == "member"
        assert response.json()["user_id"] == member["user"]["id"]

    async def test_member_cannot_add_member(self, client: AsyncClient) -> None:
        owner = await register(client, "noah@example.com", "Noah")
        member = await register(client, "olive@example.com", "Olive")
        third = await register(client, "pete@example.com", "Pete")
        org = await create_org(client, owner, "noahs")
        await client.post(
            f"/organizations/{org['id']}/members",
            json={"user_id": member["user"]["id"], "role": "member"},
            headers=bearer(owner),
        )

        response = await client.post(
            f"/organizations/{org['id']}/members",
            json={"user_id": third["user"]["id"], "role": "member"},
            headers=bearer(member),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "insufficient_role"

    async def test_duplicate_membership_conflicts(self, client: AsyncClient) -> None:
        owner = await register(client, "quinn@example.com", "Quinn")
        member = await register(client, "rosa@example.com", "Rosa")
        org = await create_org(client, owner, "quinns")
        payload = {"user_id": member["user"]["id"], "role": "member"}
        await client.post(
            f"/organizations/{org['id']}/members",
            json=payload,
            headers=bearer(owner),
        )
        response = await client.post(
            f"/organizations/{org['id']}/members",
            json=payload,
            headers=bearer(owner),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "duplicate_membership"

    async def test_member_listing(self, client: AsyncClient) -> None:
        owner = await register(client, "sam@example.com", "Sam")
        admin = await register(client, "tina@example.com", "Tina")
        org = await create_org(client, owner, "sams")
        await client.post(
            f"/organizations/{org['id']}/members",
            json={"user_id": admin["user"]["id"], "role": "admin"},
            headers=bearer(owner),
        )

        response = await client.get(f"/organizations/{org['id']}/members", headers=bearer(owner))
        assert response.status_code == 200
        members = response.json()
        roles = {m["user_id"]: m["role"] for m in members}
        assert roles[owner["user"]["id"]] == "owner"
        assert roles[admin["user"]["id"]] == "admin"
