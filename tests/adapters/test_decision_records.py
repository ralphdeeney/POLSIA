"""Durable decision record persistence tests."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

import pytest
from polsia.adapters.persistence.decision_repositories import SqlAlchemyDecisionRecordRepository
from polsia.core.ceo.decisions import Decision, DecisionType
from polsia.core.ceo.records import DecisionRecord
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
    async def test_roundtrip_and_frozen_contract(self, record_env: Any) -> None:
        repository, _session = record_env
        now = dt.datetime(2026, 9, 23, tzinfo=dt.UTC)
        record = make_record(now=now)
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
        repository, _session = record_env
        org = OrganizationId.generate()
        company = CompanyId.generate()
        other_company = CompanyId.generate()
        other_org = OrganizationId.generate()
        older = make_record(org, company, now=dt.datetime(2026, 9, 20, tzinfo=dt.UTC))
        newer = make_record(org, company, now=dt.datetime(2026, 9, 22, tzinfo=dt.UTC))
        foreign_company = make_record(org, other_company)
        foreign_org = make_record(other_org, company)
        for record in (older, newer, foreign_company, foreign_org):
            await repository.add(record)
        listed = await repository.list_for_company(org, company, limit=10)
        assert [record.id for record in listed] == [newer.id, older.id]
        assert await repository.list_for_company(org, company, limit=1) == [newer]
        assert await repository.list_for_company(other_org, other_company) == []
        assert await repository.list_for_company(org, other_company) == [foreign_company]

    async def test_scope_mismatch_and_invalid_digest_rejected(self) -> None:
        record = make_record()
        with pytest.raises(ValidationError):
            dataclasses.replace(record, company_id=CompanyId.generate())
        with pytest.raises(ValidationError):
            dataclasses.replace(record, observation_digest="  ")
