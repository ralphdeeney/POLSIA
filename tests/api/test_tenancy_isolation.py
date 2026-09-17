"""Tenancy isolation tests (CRITICAL, ADR-0002).

User B (non-member) must never see or mutate user A's organization or its
companies; token handling must reject missing/expired/garbage credentials.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from polsia.api.main import create_app
from polsia.infra.db import create_all_metadata
from polsia.infra.settings import Settings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

PASSWORD = "super-secret-1"
JWT_SECRET = "test-secret"


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
        app = create_app(Settings(env="test", jwt_secret=JWT_SECRET), session_factory=factory)
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


async def make_org_with_company(client: AsyncClient, email: str) -> tuple[dict[str, Any], Any, Any]:
    """Register a user, create an org and one company; return (user, org, company)."""
    user = await register(client, email)
    org_response = await client.post(
        "/organizations",
        json={"name": f"Org {email}", "slug": email.split("@")[0]},
        headers=bearer(user),
    )
    assert org_response.status_code == 201, org_response.text
    org = org_response.json()
    company_response = await client.post(
        f"/organizations/{org['id']}/companies",
        json={"name": "Company One", "slug": "company-one"},
        headers=bearer(user),
    )
    assert company_response.status_code == 201, company_response.text
    return user, org, company_response.json()


class TestOrganizationIsolation:
    async def test_non_member_cannot_get_org(self, client: AsyncClient) -> None:
        _, org, _ = await make_org_with_company(client, "alice@example.com")
        bob = await register(client, "bob@example.com")

        response = await client.get(f"/organizations/{org['id']}", headers=bearer(bob))
        assert response.status_code == 403
        assert response.json()["code"] == "not_a_member"

    async def test_non_member_cannot_patch_org(self, client: AsyncClient) -> None:
        _, org, _ = await make_org_with_company(client, "carol@example.com")
        bob = await register(client, "bob@example.com")

        response = await client.patch(
            f"/organizations/{org['id']}",
            json={"name": "Hijacked"},
            headers=bearer(bob),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "not_a_member"

    async def test_non_member_not_in_members_list(self, client: AsyncClient) -> None:
        alice, org, _ = await make_org_with_company(client, "dave@example.com")
        bob = await register(client, "bob@example.com")

        # Bob cannot list members either.
        denied = await client.get(f"/organizations/{org['id']}/members", headers=bearer(bob))
        assert denied.status_code == 403

        listing = await client.get(f"/organizations/{org['id']}/members", headers=bearer(alice))
        assert listing.status_code == 200
        member_ids = {m["user_id"] for m in listing.json()}
        assert bob["user"]["id"] not in member_ids

    async def test_org_listing_does_not_leak_foreign_orgs(self, client: AsyncClient) -> None:
        _, org, _ = await make_org_with_company(client, "erin@example.com")
        bob = await register(client, "bob@example.com")

        response = await client.get("/organizations", headers=bearer(bob))
        assert response.status_code == 200
        assert org["id"] not in {o["id"] for o in response.json()}


class TestCompanyIsolation:
    async def test_non_member_cannot_create_company(self, client: AsyncClient) -> None:
        _, org, _ = await make_org_with_company(client, "frank@example.com")
        bob = await register(client, "bob@example.com")

        response = await client.post(
            f"/organizations/{org['id']}/companies",
            json={"name": "Sneaky", "slug": "sneaky"},
            headers=bearer(bob),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "not_a_member"

    async def test_non_member_cannot_get_company(self, client: AsyncClient) -> None:
        _, org, company = await make_org_with_company(client, "gina@example.com")
        bob = await register(client, "bob@example.com")

        response = await client.get(
            f"/organizations/{org['id']}/companies/{company['id']}",
            headers=bearer(bob),
        )
        assert response.status_code == 403

    async def test_non_member_cannot_list_companies(self, client: AsyncClient) -> None:
        _, org, _ = await make_org_with_company(client, "henry@example.com")
        bob = await register(client, "bob@example.com")

        response = await client.get(f"/organizations/{org['id']}/companies", headers=bearer(bob))
        assert response.status_code == 403

    async def test_company_under_wrong_org_is_404(self, client: AsyncClient) -> None:
        # Bob is a member of org A but asks for a company that belongs to his
        # other org C under org A's scope -> not found (never leaked).
        alice, org_a, _ = await make_org_with_company(client, "iris@example.com")
        bob, org_c, company_c = await make_org_with_company(client, "bob@example.com")
        add = await client.post(
            f"/organizations/{org_a['id']}/members",
            json={"user_id": bob["user"]["id"], "role": "member"},
            headers=bearer(alice),
        )
        assert add.status_code == 201

        mismatched = await client.get(
            f"/organizations/{org_a['id']}/companies/{company_c['id']}",
            headers=bearer(bob),
        )
        assert mismatched.status_code == 404
        assert mismatched.json()["code"] == "company_not_found"

        # Same request under the correct organization succeeds.
        correct = await client.get(
            f"/organizations/{org_c['id']}/companies/{company_c['id']}",
            headers=bearer(bob),
        )
        assert correct.status_code == 200


class TestTokenRejection:
    async def test_missing_token_is_401(self, client: AsyncClient) -> None:
        response = await client.get("/auth/me")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_garbage_token_is_401(self, client: AsyncClient) -> None:
        response = await client.get(
            "/auth/me", headers={"Authorization": "Bearer garbage.token.here"}
        )
        assert response.status_code == 401

    async def test_expired_token_is_401(self, client: AsyncClient) -> None:
        user = await register(client, "jack@example.com")
        now = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
        expired = jwt.encode(
            {
                "sub": user["user"]["id"],
                "iat": int(now.timestamp()),
                "exp": int((now + dt.timedelta(minutes=30)).timestamp()),
                "typ": "access",
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        response = await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
        assert response.status_code == 401

    async def test_wrong_signature_is_401(self, client: AsyncClient) -> None:
        user = await register(client, "kate@example.com")
        forged = jwt.encode(
            {
                "sub": user["user"]["id"],
                "iat": int(dt.datetime.now(dt.UTC).timestamp()),
                "exp": int((dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)).timestamp()),
                "typ": "access",
            },
            "attacker-secret",
            algorithm="HS256",
        )
        response = await client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})
        assert response.status_code == 401

    async def test_refresh_token_cannot_access_apis(self, client: AsyncClient) -> None:
        user = await register(client, "leo@example.com")
        org_response = await client.get(
            "/organizations",
            headers={"Authorization": f"Bearer {user['tokens']['refresh_token']}"},
        )
        assert org_response.status_code == 401

    async def test_malformed_authorization_header_is_401(self, client: AsyncClient) -> None:
        response = await client.get("/auth/me", headers={"Authorization": "Basic abc"})
        assert response.status_code == 401
