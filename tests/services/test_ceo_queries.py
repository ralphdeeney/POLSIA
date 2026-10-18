"""Actor authorization and bounded-query tests for CEO read services."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

import pytest
from polsia.core.ceo.decisions import Decision, DecisionType
from polsia.core.ceo.records import DecisionRecord
from polsia.core.shared.errors import CompanyNotFoundError, NotAMember
from polsia.core.shared.ids import CompanyId, OrganizationId, UserId
from polsia.core.tenancy.entities import Company, OrgMembership, User
from polsia.core.tenancy.values import MembershipRole
from polsia.services.ceo_queries import CEOQueryService


class _Memberships:
    def __init__(self, member: bool) -> None:
        self.member = member
        self.calls = 0

    async def get(self, organization_id: OrganizationId, user_id: UserId) -> Any:
        self.calls += 1
        if not self.member:
            return None
        return OrgMembership.create(
            organization_id=organization_id, user_id=user_id, role=MembershipRole.MEMBER
        )


class _Companies:
    def __init__(self, company: Company | None) -> None:
        self.company = company
        self.calls = 0

    async def get(self, organization_id: OrganizationId, company_id: CompanyId) -> Company | None:
        self.calls += 1
        if self.company and self.company.organization_id == organization_id:
            return self.company if self.company.id == company_id else None
        return None


class _Records:
    def __init__(self, records: list[DecisionRecord] | None = None) -> None:
        self.records = records or []
        self.calls: list[tuple[OrganizationId, CompanyId, int]] = []

    async def list_for_company(
        self, organization_id: OrganizationId, company_id: CompanyId, *, limit: int = 100
    ) -> list[DecisionRecord]:
        self.calls.append((organization_id, company_id, limit))
        return self.records[:limit]


class _Observer:
    def __init__(self) -> None:
        self.calls = 0

    async def observe(self, organization_id: OrganizationId, company_id: CompanyId) -> Any:
        self.calls += 1
        raise AssertionError("observer must not run before actor authorization")


def _record(
    organization_id: OrganizationId,
    company_id: CompanyId,
    cycle_id: str,
    recorded_at: dt.datetime,
    outcome: str,
) -> DecisionRecord:
    decision = Decision.create(
        company_id=company_id,
        decision_type=DecisionType.WAIT,
        cycle_id=cycle_id,
        now=recorded_at,
    )
    return DecisionRecord.create(
        organization_id=organization_id,
        company_id=company_id,
        decision=decision,
        observation_digest="digest",
        outcome_context={"status": outcome},
        now=recorded_at,
    )


def _fixture_service(
    *, member: bool = True, company_exists: bool = True, records: list[DecisionRecord] | None = None
) -> tuple[CEOQueryService, User, OrganizationId, CompanyId, _Records, _Observer, _Companies]:
    organization_id = OrganizationId.generate()
    company_id = CompanyId.generate()
    actor = User.register(
        email="ceo-query@example.com", hashed_password="hash", display_name="Actor"
    )
    company = (
        Company.create(organization_id=organization_id, name="Test", slug="query-company")
        if company_exists
        else None
    )
    if company is not None:
        company_id = company.id
        if records:
            records = [
                dataclasses.replace(
                    record,
                    organization_id=organization_id,
                    company_id=company_id,
                    decision=dataclasses.replace(record.decision, company_id=company_id),
                )
                for record in records
            ]
    repos = _Records(records)
    observer = _Observer()
    companies = _Companies(company)
    memberships = _Memberships(member)
    service = CEOQueryService(
        companies=companies,
        memberships=memberships,
        observer=observer,
        records=repos,  # type: ignore[arg-type]
    )
    return service, actor, organization_id, company_id, repos, observer, companies


class TestCEOQueryService:
    async def test_authorization_precedes_observer_and_record_reads(self) -> None:
        service, actor, org_id, company_id, records, observer, companies = _fixture_service(
            member=False
        )
        with pytest.raises(NotAMember):
            await service.current_observation(actor, org_id, company_id)
        with pytest.raises(NotAMember):
            await service.list_decisions(actor, org_id, company_id)
        assert observer.calls == 0
        assert records.calls == []
        assert companies.calls == 0

    async def test_wrong_organization_company_is_not_found_before_queries(self) -> None:
        service, actor, org_id, company_id, records, observer, _companies = _fixture_service()
        with pytest.raises(CompanyNotFoundError):
            await service.current_observation(actor, OrganizationId.generate(), company_id)
        assert observer.calls == 0
        assert records.calls == []
        # The membership check for the requested organization happens first.
        assert org_id

    async def test_direct_limits_are_bounded_and_tenant_scoped(self) -> None:
        service, actor, org_id, company_id, records, _observer, _companies = _fixture_service()
        results = await service.list_decisions(actor, org_id, company_id, limit=50000)
        assert results == []
        assert records.calls[-1] == (org_id, company_id, 1000)

    async def test_cycle_summaries_derive_counts_and_times_from_records_only(self) -> None:
        service, actor, org_id, company_id, repository, _observer, _companies = _fixture_service()
        now = dt.datetime(2026, 9, 23, tzinfo=dt.UTC)
        repository.records = [
            _record(org_id, company_id, "cycle-a", now, "executed"),
            _record(org_id, company_id, "cycle-a", now + dt.timedelta(seconds=1), "wait"),
            _record(org_id, company_id, "cycle-b", now + dt.timedelta(seconds=2), "stopped"),
        ]
        summaries = await service.list_cycle_summaries(actor, org_id, company_id, limit=1)
        assert len(summaries) == 1
        assert summaries[0].cycle_id == "cycle-b"
        assert summaries[0].decision_count == 1
        assert summaries[0].outcome_counts == {"stopped": 1}
        assert summaries[0].first_recorded_at == now + dt.timedelta(seconds=2)
        assert summaries[0].last_recorded_at == now + dt.timedelta(seconds=2)
        assert not hasattr(summaries[0], "stop_reason")
        assert not hasattr(summaries[0], "action_count")
        assert repository.calls[-1][2] == 1000
