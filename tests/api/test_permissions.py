"""Permission/approval endpoint tests (Phase 2, Slice 4).

Full auth flows over the API: policy CRUD (member-only), pure check endpoint,
approval request/decide lifecycle and cross-tenant isolation.
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
        json={"name": "Perm Co", "slug": "perm-co"},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return user, org, response.json()


def perms_url(org_id: str, company_id: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/permissions"


def approvals_url(org_id: str, company_id: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/approvals"


ALLOW_POLICY = {
    "principal_type": "agent",
    "principal_id": "agt_01TESTAGENT",
    "resource_type": "tool",
    "resource_id": "web.search@1.0.0",
    "action": "web.search",
    "decision": "allow",
}


class TestPermissionPolicies:
    async def test_set_and_list_policy(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "perma@example.com")
        response = await client.put(
            perms_url(org["id"], company["id"]), json=ALLOW_POLICY, headers=bearer(user)
        )
        assert response.status_code == 200, response.text
        policy = response.json()
        assert policy["decision"] == "allow"
        assert policy["principal_type"] == "agent"
        assert policy["action"] == "web.search"
        # UPSERT: replacing the same rule does not create a second one.
        response = await client.put(
            perms_url(org["id"], company["id"]),
            json={**ALLOW_POLICY, "decision": "deny", "constraints": {"window": "9-17"}},
            headers=bearer(user),
        )
        assert response.status_code == 200, response.text
        response = await client.get(perms_url(org["id"], company["id"]), headers=bearer(user))
        assert response.status_code == 200
        policies = response.json()
        assert len(policies) == 1
        assert policies[0]["decision"] == "deny"
        assert policies[0]["constraints"] == {"window": "9-17"}

    async def test_unauthenticated_set_policy_401(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "permb@example.com")
        response = await client.put(perms_url(org["id"], company["id"]), json=ALLOW_POLICY)
        assert response.status_code == 401

    async def test_check_endpoint_returns_decision(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "permc@example.com")
        await client.put(
            perms_url(org["id"], company["id"]), json=ALLOW_POLICY, headers=bearer(user)
        )
        check_body = {
            "principal_type": "agent",
            "principal_id": "agt_01TESTAGENT",
            "resource_type": "tool",
            "resource_id": "web.search@1.0.0",
            "action": "web.search",
        }
        response = await client.post(
            perms_url(org["id"], company["id"]) + "/check",
            json=check_body,
            headers=bearer(user),
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"decision": "allow"}
        # No matching rule -> default deny.
        response = await client.post(
            perms_url(org["id"], company["id"]) + "/check",
            json={**check_body, "action": "email.send"},
            headers=bearer(user),
        )
        assert response.json() == {"decision": "deny"}

    async def test_check_endpoint_unauthenticated_401(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "permd@example.com")
        response = await client.post(
            perms_url(org["id"], company["id"]) + "/check",
            json={
                "principal_type": "agent",
                "principal_id": "agt_01X",
                "resource_type": "tool",
                "resource_id": "web.search@1.0.0",
                "action": "web.search",
            },
        )
        assert response.status_code == 401

    async def test_cross_tenant_isolation(self, client: AsyncClient) -> None:
        user_a, org_a, company_a = await make_company(client, "perme@example.com")
        await client.put(
            perms_url(org_a["id"], company_a["id"]), json=ALLOW_POLICY, headers=bearer(user_a)
        )
        intruder = await register(client, "permf@example.com")
        response = await client.get(
            perms_url(org_a["id"], company_a["id"]), headers=bearer(intruder)
        )
        assert response.status_code == 403
        response = await client.get(
            approvals_url(org_a["id"], company_a["id"]), headers=bearer(intruder)
        )
        assert response.status_code == 403
        response = await client.post(
            approvals_url(org_a["id"], company_a["id"]),
            json={
                "requested_by": intruder["user"]["id"],
                "action": "web.search",
                "resource_type": "tool",
            },
            headers=bearer(intruder),
        )
        assert response.status_code == 403


class TestApprovals:
    async def test_request_and_decide_happy_path(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "permg@example.com")
        response = await client.post(
            approvals_url(org["id"], company["id"]),
            json={
                "requested_by": user["user"]["id"],
                "action": "payments.transfer",
                "resource_type": "tool",
                "resource_id": "payments@1.0.0",
                "reason": "large amount",
            },
            headers=bearer(user),
        )
        assert response.status_code == 201, response.text
        approval = response.json()
        assert approval["status"] == "pending"
        assert approval["requested_by"] == user["user"]["id"]
        assert approval["decided_by"] is None

        response = await client.post(
            approvals_url(org["id"], company["id"]) + f"/{approval['id']}/decide",
            json={"decision": "approved"},
            headers=bearer(user),
        )
        assert response.status_code == 200, response.text
        decided = response.json()
        assert decided["status"] == "approved"
        assert decided["decided_by"] == user["user"]["id"]
        assert decided["decided_at"] is not None

        # Deciding a non-pending approval is a 422 domain-rule error.
        response = await client.post(
            approvals_url(org["id"], company["id"]) + f"/{approval['id']}/decide",
            json={"decision": "rejected"},
            headers=bearer(user),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_status_transition"

    async def test_reject_and_pending_only(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "permh@example.com")
        response = await client.post(
            approvals_url(org["id"], company["id"]),
            json={
                "requested_by": user["user"]["id"],
                "action": "email.send",
                "resource_type": "tool",
            },
            headers=bearer(user),
        )
        assert response.status_code == 201, response.text
        approval = response.json()
        response = await client.post(
            approvals_url(org["id"], company["id"]) + f"/{approval['id']}/decide",
            json={"decision": "rejected"},
            headers=bearer(user),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

        response = await client.get(
            approvals_url(org["id"], company["id"]) + "?pending_only=true",
            headers=bearer(user),
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_decide_unknown_approval_404(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "permi@example.com")
        response = await client.post(
            approvals_url(org["id"], company["id"]) + "/apr_01UNKNOWN/decide",
            json={"decision": "approved"},
            headers=bearer(user),
        )
        assert response.status_code == 404

    async def test_invalid_decision_value_422(self, client: AsyncClient) -> None:
        user, org, company = await make_company(client, "permj@example.com")
        response = await client.post(
            approvals_url(org["id"], company["id"]) + "/apr_01X/decide",
            json={"decision": "maybe"},
            headers=bearer(user),
        )
        assert response.status_code == 422
