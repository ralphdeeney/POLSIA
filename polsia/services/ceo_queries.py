"""Actor-authorized, read-only CEO query use cases.

All reads verify organization membership and company ownership before touching
observation or decision-record data. Cycle summaries are derived from the
bounded set of persisted DecisionRecords; no loop execution metadata is
inferred or exposed.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

from polsia.core.ceo.observation import CompanyObservation
from polsia.core.ceo.records import DecisionRecord, DecisionRecordRepository
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.ids import CompanyId, OrganizationId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.observation import CompanyStateObserver
from polsia.services.planning import _CompanyScope

MAX_QUERY_LIMIT = 1000


class CEOQueryService:
    """Read-only CEO history and current-state queries for one tenant scope."""

    def __init__(
        self,
        *,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        observer: CompanyStateObserver,
        records: DecisionRecordRepository,
    ) -> None:
        self._scope = _CompanyScope(companies, memberships)
        self._observer = observer
        self._records = records

    async def list_decisions(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        limit: int = 100,
    ) -> list[DecisionRecord]:
        await self._authorize(actor, organization_id, company_id)
        return await self._records.list_for_company(
            organization_id, company_id, limit=_bounded_limit(limit)
        )

    async def list_cycle_summaries(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        limit: int = 100,
    ) -> list[CycleSummary]:
        await self._authorize(actor, organization_id, company_id)
        # The repository has a hard 1000-record ceiling. Grouping this bounded
        # result avoids an unbounded history read while using only persisted
        # DecisionRecord fields.
        records = await self._records.list_for_company(
            organization_id, company_id, limit=MAX_QUERY_LIMIT
        )
        grouped: dict[str, list[DecisionRecord]] = defaultdict(list)
        for record in records:
            cycle_id = record.decision.cycle_id
            if cycle_id is not None:
                grouped[cycle_id].append(record)

        summaries: list[CycleSummary] = []
        for cycle_id, cycle_records in grouped.items():
            outcome_counts: dict[str, int] = {}
            timestamps = [record.recorded_at for record in cycle_records]
            for record in cycle_records:
                outcome = record.outcome_context.get("status")
                key = outcome if isinstance(outcome, str) and outcome else "unspecified"
                outcome_counts[key] = outcome_counts.get(key, 0) + 1
            summaries.append(
                CycleSummary(
                    cycle_id=cycle_id,
                    decision_count=len(cycle_records),
                    outcome_counts=outcome_counts,
                    first_recorded_at=min(timestamps),
                    last_recorded_at=max(timestamps),
                )
            )
        summaries.sort(
            key=lambda summary: (summary.last_recorded_at, summary.cycle_id), reverse=True
        )
        return summaries[: _bounded_limit(limit)]

    async def current_observation(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
    ) -> CompanyObservation:
        await self._authorize(actor, organization_id, company_id)
        observation = await self._observer.observe(organization_id, company_id)
        if observation.organization_id != organization_id or observation.company_id != company_id:
            raise ValidationError("observer returned a mismatched tenant scope")
        return observation

    async def _authorize(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId
    ) -> None:
        await self._scope.require_company(actor, organization_id, company_id)


@dataclass(frozen=True)
class CycleSummary:
    """Values derived solely from persisted decision records."""

    cycle_id: str
    decision_count: int
    outcome_counts: dict[str, int]
    first_recorded_at: dt.datetime
    last_recorded_at: dt.datetime


def _bounded_limit(limit: int) -> int:
    """Keep direct service callers inside the repository's hard query bound."""
    if not isinstance(limit, int):
        raise ValidationError("query limit must be an integer")
    return max(1, min(limit, MAX_QUERY_LIMIT))
