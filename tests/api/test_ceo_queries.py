"""CEO API query boundary tests: auth, tenant scope, bounds, and GET-only routes."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from polsia.adapters.persistence.decision_repositories import SqlAlchemyDecisionRecordRepository
from polsia.api.main import create_app
from polsia.api.schemas import DecisionRecordOut
from polsia.core.ceo.decisions import Decision, DecisionType
from polsia.core.ceo.records import MAX_DECISION_RECORD_JSON_BYTES, DecisionRecord
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.ids import CompanyId, OrganizationId
from polsia.infra.db import create_all_metadata
from polsia.infra.settings import Settings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

PASSWORD = "super-secret-1"


@pytest.fixture
async def client() -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        app = create_app(Settings(env="test", jwt_secret="test-secret"), session_factory=factory)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http, app, factory
    finally:
        await engine.dispose()


async def register(http: AsyncClient, email: str) -> dict[str, Any]:
    response = await http.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(user: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['tokens']['access_token']}"}


async def create_org_company(
    http: AsyncClient, user: dict[str, Any], suffix: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = await http.post(
        "/organizations",
        json={"name": f"Org {suffix}", "slug": f"org-{suffix}"},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    organization = response.json()
    response = await http.post(
        f"/organizations/{organization['id']}/companies",
        json={"name": f"Company {suffix}", "slug": f"company-{suffix}"},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return organization, response.json()


def ceo_url(org_id: str, company_id: str, endpoint: str) -> str:
    return f"/organizations/{org_id}/companies/{company_id}/ceo/{endpoint}"


async def add_wait_record(
    factory: async_sessionmaker[AsyncSession], organization_id: str, company_id: str
) -> DecisionRecord:
    now = dt.datetime(2026, 9, 23, tzinfo=dt.UTC)
    decision = Decision.create(
        company_id=CompanyId.parse(company_id),
        decision_type=DecisionType.WAIT,
        cycle_id="cycle-read-only-1",
        rationale="No work",
        now=now,
    )
    record = DecisionRecord.create(
        organization_id=OrganizationId.parse(organization_id),
        company_id=decision.company_id,
        decision=decision,
        observation_digest="persisted-digest",
        outcome_context={"status": "stopped", "reason": "no_eligible_work"},
        now=now,
    )
    async with factory() as session:
        await SqlAlchemyDecisionRecordRepository(session).add(record)
        await session.commit()
    return record


class TestCEOQueryApi:
    def test_decision_record_dto_enforces_combined_json_ceiling(self) -> None:
        fields = {
            "id": "dcr_test",
            "organization_id": "org_test",
            "company_id": "cmp_test",
            "decision_type": "wait",
            "payload": {"text": "x" * 9000},
            "rationale": None,
            "confidence": None,
            "model_reference": None,
            "cycle_id": None,
            "task_id": None,
            "decision_created_at": dt.datetime.now(dt.UTC),
            "observation_digest": "digest",
            "outcome_context": {},
            "recorded_at": dt.datetime.now(dt.UTC),
        }
        with pytest.raises(ValidationError, match="exceed"):
            DecisionRecordOut.model_validate(fields)

    def test_decision_record_dto_accepts_exact_combined_json_ceiling(self) -> None:
        base_size = len(
            json.dumps(
                {"payload": {"text": ""}, "outcome_context": {}},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        payload = {"text": "x" * (MAX_DECISION_RECORD_JSON_BYTES - base_size)}
        fields = {
            "id": "dcr_test",
            "organization_id": "org_test",
            "company_id": "cmp_test",
            "decision_type": "wait",
            "payload": payload,
            "rationale": None,
            "confidence": None,
            "model_reference": None,
            "cycle_id": None,
            "task_id": None,
            "decision_created_at": dt.datetime.now(dt.UTC),
            "observation_digest": "digest",
            "outcome_context": {},
            "recorded_at": dt.datetime.now(dt.UTC),
        }
        dto = DecisionRecordOut.model_validate(fields)
        assert dto.payload == payload

    async def test_authenticated_reads_return_dto_summaries(self, client: Any) -> None:
        http, _app, factory = client
        owner = await register(http, "ceo-read-owner@example.com")
        org, company = await create_org_company(http, owner, "ceo-read")
        record = await add_wait_record(factory, org["id"], company["id"])

        decisions = await http.get(
            ceo_url(org["id"], company["id"], "decisions"), headers=bearer(owner)
        )
        assert decisions.status_code == 200, decisions.text
        assert len(decisions.json()) == 1
        body = decisions.json()[0]
        assert body["id"] == str(record.id)
        assert body["decision_type"] == "wait"
        assert body["outcome_context"]["status"] == "stopped"

        cycles = await http.get(ceo_url(org["id"], company["id"], "cycles"), headers=bearer(owner))
        assert cycles.status_code == 200, cycles.text
        cycle = cycles.json()[0]
        assert cycle["cycle_id"] == "cycle-read-only-1"
        assert cycle["decision_count"] == 1
        assert cycle["outcome_counts"] == {"stopped": 1}
        assert "stop_reason" not in cycle
        assert "action_count" not in cycle

        observation = await http.get(
            ceo_url(org["id"], company["id"], "observation"), headers=bearer(owner)
        )
        assert observation.status_code == 200, observation.text
        summary = observation.json()
        assert summary["organization_id"] == org["id"]
        assert summary["company_id"] == company["id"]
        assert summary["goals_total"] == 0
        assert summary["kpis_total"] == 0
        assert summary["tasks_total"] == 0
        assert "digest" in summary

    async def test_missing_and_invalid_credentials_are_rejected(self, client: Any) -> None:
        http, _app, _factory = client
        owner = await register(http, "ceo-read-auth@example.com")
        org, company = await create_org_company(http, owner, "ceo-auth")
        url = ceo_url(org["id"], company["id"], "decisions")
        missing = await http.get(url)
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        invalid = await http.get(url, headers={"Authorization": "Bearer garbage"})
        assert invalid.status_code == 401

    async def test_nonmember_and_wrong_company_are_denied_without_leakage(
        self, client: Any
    ) -> None:
        http, _app, factory = client
        owner_a = await register(http, "ceo-read-a@example.com")
        org_a, company_a = await create_org_company(http, owner_a, "ceo-a")
        await add_wait_record(factory, org_a["id"], company_a["id"])
        outsider = await register(http, "ceo-read-outsider@example.com")

        denied = await http.get(
            ceo_url(org_a["id"], company_a["id"], "decisions"), headers=bearer(outsider)
        )
        assert denied.status_code == 403

        owner_b = await register(http, "ceo-read-b@example.com")
        org_b, company_b = await create_org_company(http, owner_b, "ceo-b")
        membership = await http.post(
            f"/organizations/{org_a['id']}/members",
            json={"user_id": owner_b["user"]["id"], "role": "member"},
            headers=bearer(owner_a),
        )
        assert membership.status_code == 201, membership.text
        mismatched = await http.get(
            ceo_url(org_a["id"], company_b["id"], "decisions"), headers=bearer(owner_b)
        )
        assert mismatched.status_code == 404
        assert mismatched.json()["code"] == "company_not_found"

        own = await http.get(
            ceo_url(org_b["id"], company_b["id"], "decisions"), headers=bearer(owner_b)
        )
        assert own.status_code == 200
        assert own.json() == []

    async def test_limits_are_validated_and_results_bounded(self, client: Any) -> None:
        http, _app, factory = client
        owner = await register(http, "ceo-read-limit@example.com")
        org, company = await create_org_company(http, owner, "ceo-limit")
        await add_wait_record(factory, org["id"], company["id"])
        url = ceo_url(org["id"], company["id"], "decisions")
        response = await http.get(f"{url}?limit=1", headers=bearer(owner))
        assert response.status_code == 200
        assert len(response.json()) == 1
        invalid = await http.get(f"{url}?limit=1001", headers=bearer(owner))
        assert invalid.status_code == 422

    async def test_ceo_surface_is_get_only_and_has_no_trigger_route(self, client: Any) -> None:
        http, app, _factory = client
        ceo_path = "/organizations/{org_id}/companies/{company_id}/ceo"
        ceo_paths = {
            path: operations
            for path, operations in app.openapi()["paths"].items()
            if path.startswith(ceo_path)
        }
        assert set(ceo_paths) == {
            f"{ceo_path}/decisions",
            f"{ceo_path}/cycles",
            f"{ceo_path}/observation",
        }
        assert all(set(operations) == {"get"} for operations in ceo_paths.values())
        assert all("trigger" not in path and "execute" not in path for path in ceo_paths)

        user = await register(http, "ceo-read-method@example.com")
        org, company = await create_org_company(http, user, "ceo-method")
        url = ceo_url(org["id"], company["id"], "decisions")
        response = await http.post(url, headers=bearer(user))
        assert response.status_code == 405
