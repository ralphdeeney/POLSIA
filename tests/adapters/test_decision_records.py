"""Durable decision record persistence tests."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from typing import Any

import pytest
from polsia.adapters.persistence.decision_repositories import SqlAlchemyDecisionRecordRepository
from polsia.adapters.persistence.models import CompanyModel
from polsia.core.ceo.decisions import Decision, DecisionType
from polsia.core.ceo.records import MAX_DECISION_RECORD_JSON_BYTES, DecisionRecord
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.ids import CompanyId, OrganizationId
from polsia.infra.db import create_all_metadata
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def record_env():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            yield SqlAlchemyDecisionRecordRepository(session), session
    finally:
        await engine.dispose()


async def add_company_row(
    session: Any, organization_id: OrganizationId, company_id: CompanyId
) -> None:
    """Persist the owning company row required by the record ownership check."""
    session.add(
        CompanyModel(
            id=str(company_id),
            organization_id=str(organization_id),
            name="Record Co",
            slug=f"record-co-{str(company_id).lower()[-8:]}",
            identity={},
            strategy={},
            configuration={},
            status="active",
        )
    )
    await session.flush()


def make_record(
    organization_id: OrganizationId | None = None,
    company_id: CompanyId | None = None,
    *,
    now: dt.datetime | None = None,
    digest: str = "state-digest",
) -> DecisionRecord:
    org = organization_id or OrganizationId.generate()
    company = company_id or CompanyId.generate()
    decision = Decision.create(
        company_id=company,
        decision_type=DecisionType.WAIT,
        payload={"nested": {"values": [1, "two"]}},
        rationale="No eligible task",
        confidence=0.75,
        model_reference="deterministic@1.0.0",
        cycle_id="cycle-1",
        now=now,
    )
    return DecisionRecord.create(
        organization_id=org,
        company_id=company,
        decision=decision,
        observation_digest=digest,
        outcome_context={"status": "wait", "detail": ["none"]},
        now=now,
    )


class TestDecisionRecordPersistence:
    def test_combined_json_size_limit_accepts_exact_boundary(self) -> None:
        payload = {
            "data": "x"
            * (MAX_DECISION_RECORD_JSON_BYTES - len('{"outcome_context":{},"payload":{"data":""}}'))
        }
        assert (
            len(
                json.dumps(
                    {"payload": payload, "outcome_context": {}},
                    allow_nan=False,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            == MAX_DECISION_RECORD_JSON_BYTES
        )
        company_id = CompanyId.generate()
        decision = Decision.create(company_id=company_id, decision_type=DecisionType.WAIT)
        decision = dataclasses.replace(decision, payload=payload)
        assert DecisionRecord.create(
            organization_id=OrganizationId.generate(),
            company_id=company_id,
            decision=decision,
            observation_digest="digest",
        )

    def test_combined_json_size_limit_rejects_oversize_and_invalid_json(self) -> None:
        company_id = CompanyId.generate()
        base = Decision.create(company_id=company_id, decision_type=DecisionType.WAIT)
        with pytest.raises(ValidationError, match="exceed"):
            DecisionRecord.create(
                organization_id=OrganizationId.generate(),
                company_id=company_id,
                decision=dataclasses.replace(base, payload={"data": "x" * 9000}),
                observation_digest="digest",
                outcome_context={"extra": "y"},
            )
        with pytest.raises(ValidationError, match="JSON serializable"):
            DecisionRecord.create(
                organization_id=OrganizationId.generate(),
                company_id=company_id,
                decision=dataclasses.replace(base, payload={"not_finite": float("nan")}),
                observation_digest="digest",
            )
        recursive: dict[str, Any] = {}
        recursive["self"] = recursive
        with pytest.raises(ValidationError):
            DecisionRecord.create(
                organization_id=OrganizationId.generate(),
                company_id=company_id,
                decision=dataclasses.replace(base, payload=recursive),
                observation_digest="digest",
            )

    async def test_roundtrip_and_frozen_contract(self, record_env: Any) -> None:
        repository, session = record_env
        now = dt.datetime(2026, 9, 23, tzinfo=dt.UTC)
        record = make_record(now=now)
        await add_company_row(session, record.organization_id, record.company_id)
        await repository.add(record)
        loaded = await repository.list_for_company(record.organization_id, record.company_id)
        assert loaded == [record]
        assert loaded[0].decision.payload == {"nested": {"values": [1, "two"]}}
        assert loaded[0].outcome_context == {"status": "wait", "detail": ["none"]}
        assert loaded[0].decision.created_at == now
        assert loaded[0].recorded_at == now
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.observation_digest = "changed"  # type: ignore[misc]
        assert not hasattr(repository, "update")
        assert not hasattr(repository, "delete")

    async def test_ordering_limit_and_tenant_isolation(self, record_env: Any) -> None:
        repository, session = record_env
        org = OrganizationId.generate()
        company = CompanyId.generate()
        other_org = OrganizationId.generate()
        other_company = CompanyId.generate()
        await add_company_row(session, org, company)
        await add_company_row(session, other_org, other_company)
        older = make_record(org, company, now=dt.datetime(2026, 9, 20, tzinfo=dt.UTC))
        newer = make_record(org, company, now=dt.datetime(2026, 9, 22, tzinfo=dt.UTC))
        other_org_record = make_record(other_org, other_company)
        for record in (older, newer, other_org_record):
            await repository.add(record)
        listed = await repository.list_for_company(org, company, limit=10)
        assert [record.id for record in listed] == [newer.id, older.id]
        assert await repository.list_for_company(org, company, limit=1) == [newer]
        assert await repository.list_for_company(other_org, other_company) == [other_org_record]
        assert await repository.list_for_company(org, other_company) == []
        assert await repository.list_for_company(other_org, company) == []

    async def test_cross_tenant_org_company_pair_rejected(self, record_env: Any) -> None:
        """A record whose company is not owned by the record organization is rejected."""
        repository, session = record_env
        org = OrganizationId.generate()
        company = CompanyId.generate()
        other_org = OrganizationId.generate()
        await add_company_row(session, org, company)
        # the company exists, but belongs to a different organization
        with pytest.raises(ValidationError, match="does not belong"):
            await repository.add(make_record(other_org, company))
        # the company does not exist in the session at all
        with pytest.raises(ValidationError, match="does not belong"):
            await repository.add(make_record(org, CompanyId.generate()))

    async def test_outcome_context_must_be_json_object(self) -> None:
        company_id = CompanyId.generate()
        base = Decision.create(company_id=company_id, decision_type=DecisionType.WAIT)
        invalid_values: list[Any] = [
            ["not", "an", "object"],
            [["looks", "like-pairs"]],
            "wait",
            42,
        ]
        for value in invalid_values:
            with pytest.raises(ValidationError, match="JSON object"):
                DecisionRecord.create(
                    organization_id=OrganizationId.generate(),
                    company_id=company_id,
                    decision=base,
                    observation_digest="digest",
                    outcome_context=value,  # type: ignore[arg-type]
                )

    async def test_repository_revalidates_mutated_nested_json(self, record_env: Any) -> None:
        repository, session = record_env
        record = make_record()
        await add_company_row(session, record.organization_id, record.company_id)
        # frozen dataclass prevents rebinding, not mutation inside nested dicts
        record.decision.payload["late"] = float("nan")
        with pytest.raises(ValidationError):
            await repository.add(record)
        await session.rollback()

    async def test_scope_mismatch_and_invalid_digest_rejected(self) -> None:
        record = make_record()
        with pytest.raises(ValidationError):
            dataclasses.replace(record, company_id=CompanyId.generate())
        with pytest.raises(ValidationError):
            dataclasses.replace(record, observation_digest="  ")
