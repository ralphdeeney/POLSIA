"""Business intake / blueprint / bootstrap API endpoint tests.

Covers the full workflow over HTTP, RFC 9457 problem details for the error
paths (404/409/422), and cross-tenant isolation at the API boundary.
"""

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


async def make_org(client: AsyncClient, email: str) -> tuple[dict[str, Any], dict[str, Any]]:
    user = await register(client, email)
    response = await client.post(
        "/organizations",
        json={"name": f"Org of {email}", "slug": email.split("@")[0]},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return user, response.json()


async def submit_idea(
    client: AsyncClient,
    user: dict[str, Any],
    org_id: str,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "raw_idea": "A scheduling SaaS for independent tutors",
        **extra,
    }
    response = await client.post(
        f"/organizations/{org_id}/business-ideas", json=payload, headers=bearer(user)
    )
    return {"status": response.status_code, "body": response.json()}


class TestIntake:
    async def test_submit_and_fetch_idea(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "intake@example.com")
        created = await submit_idea(client, user, org["id"], target_market="tutors")
        assert created["status"] == 201
        idea = created["body"]
        assert idea["intake_status"] == "validated"
        assert idea["blueprint_status"] == "draft"
        assert idea["raw_idea"] == "A scheduling SaaS for independent tutors"

        fetched = await client.get(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}", headers=bearer(user)
        )
        assert fetched.status_code == 200
        assert fetched.json()["id"] == idea["id"]

    async def test_multiple_independent_ideas_per_org(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "multi@example.com")
        first = await submit_idea(client, user, org["id"], raw_idea="Idea one")
        second = await submit_idea(client, user, org["id"], raw_idea="Idea two")
        assert first["body"]["id"] != second["body"]["id"]
        listed = await client.get(
            f"/organizations/{org['id']}/business-ideas", headers=bearer(user)
        )
        assert listed.status_code == 200
        assert len(listed.json()) == 2

    async def test_repeat_with_idempotency_key_reuses_idea(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "idem@example.com")
        first = await submit_idea(client, user, org["id"], idempotency_key="k-1")
        second = await submit_idea(client, user, org["id"], idempotency_key="k-1")
        assert first["body"]["id"] == second["body"]["id"]

    async def test_same_key_different_payload_is_conflict(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "idem2@example.com")
        await submit_idea(client, user, org["id"], raw_idea="First", idempotency_key="k-2")
        conflict = await submit_idea(
            client, user, org["id"], raw_idea="Different", idempotency_key="k-2"
        )
        assert conflict["status"] == 409
        assert conflict["body"]["code"] == "duplicate_business_idea"

    async def test_empty_idea_is_rejected_422(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "empty@example.com")
        response = await client.post(
            f"/organizations/{org['id']}/business-ideas",
            json={"raw_idea": ""},
            headers=bearer(user),
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"

    async def test_unauthenticated_submission_is_401(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "anon@example.com")
        response = await client.post(
            f"/organizations/{org['id']}/business-ideas", json={"raw_idea": "Nope"}
        )
        assert response.status_code == 401

    async def test_foreign_org_submission_denied(self, client: AsyncClient) -> None:
        _owner, org = await make_org(client, "owner-a@example.com")
        intruder, _intruder_org = await make_org(client, "intruder@example.com")
        response = await client.post(
            f"/organizations/{org['id']}/business-ideas",
            json={"raw_idea": "Cross tenant"},
            headers=bearer(intruder),
        )
        assert response.status_code in (403, 404)


class TestBlueprintAndBootstrap:
    async def test_generate_blueprint(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "blueprint@example.com")
        idea = (await submit_idea(client, user, org["id"]))["body"]
        response = await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/blueprint",
            headers=bearer(user),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["blueprint_status"] == "validated"
        assert body["source"] == "deterministic"
        assert body["research_used"] is False
        assert body["blueprint"]["initial_goals"]

    async def test_bootstrap_creates_company(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "boot@example.com")
        idea = (await submit_idea(client, user, org["id"]))["body"]
        await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/blueprint",
            headers=bearer(user),
        )
        response = await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/bootstrap",
            headers=bearer(user),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["planning_ready"] is True
        assert body["reused"] is False
        assert len(body["goal_ids"]) == 4
        assert len(body["agent_ids"]) == 2
        # The company is real and owned by this organization.
        company = await client.get(
            f"/organizations/{org['id']}/companies/{body['company_id']}", headers=bearer(user)
        )
        assert company.status_code == 200
        assert company.json()["organization_id"] == org["id"]

    async def test_bootstrap_without_blueprint_is_422(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "nobp@example.com")
        idea = (await submit_idea(client, user, org["id"]))["body"]
        response = await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/bootstrap",
            headers=bearer(user),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "blueprint_not_validated"

    async def test_repeat_bootstrap_reuses_company(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "bootidem@example.com")
        idea = (await submit_idea(client, user, org["id"]))["body"]
        await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/blueprint",
            headers=bearer(user),
        )
        first = await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/bootstrap",
            headers=bearer(user),
        )
        second = await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/bootstrap",
            headers=bearer(user),
        )
        assert first.json()["company_id"] == second.json()["company_id"]
        assert second.json()["reused"] is True

    async def test_ceo_initialization_and_initial_plan(self, client: AsyncClient) -> None:
        user, org = await make_org(client, "ceo@example.com")
        idea = (await submit_idea(client, user, org["id"]))["body"]
        await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/blueprint",
            headers=bearer(user),
        )
        ceo = await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/ceo",
            headers=bearer(user),
        )
        assert ceo.status_code == 201, ceo.text
        assert ceo.json()["agent_name"]

        plan = await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/initial-plan",
            headers=bearer(user),
        )
        assert plan.status_code == 201, plan.text
        body = plan.json()
        assert body["task_ids"]
        assert body["objective"]

    async def test_cross_tenant_cannot_read_blueprint(self, client: AsyncClient) -> None:
        owner, org = await make_org(client, "bpowner@example.com")
        idea = (await submit_idea(client, owner, org["id"]))["body"]
        await client.post(
            f"/organizations/{org['id']}/business-ideas/{idea['id']}/blueprint",
            headers=bearer(owner),
        )
        intruder, intruder_org = await make_org(client, "bpintruder@example.com")
        # Same idea id, foreign organization scope => not found.
        response = await client.get(
            f"/organizations/{intruder_org['id']}/business-ideas/{idea['id']}/blueprint",
            headers=bearer(intruder),
        )
        assert response.status_code in (403, 404)
