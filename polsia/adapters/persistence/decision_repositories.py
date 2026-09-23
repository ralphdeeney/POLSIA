"""SQLAlchemy repository for append-only, tenant-scoped decision records."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.decision_models import DecisionRecordModel
from polsia.core.ceo.decisions import Decision, DecisionType
from polsia.core.ceo.records import DecisionRecord
from polsia.core.shared.ids import CompanyId, DecisionRecordId, OrganizationId


def _aware(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


def _to_record(row: DecisionRecordModel) -> DecisionRecord:
    decision = Decision.create(
        company_id=CompanyId.parse(row.company_id),
        decision_type=DecisionType(row.decision_type),
        payload=dict(row.payload or {}),
        rationale=row.rationale,
        confidence=row.confidence,
        model_reference=row.model_reference,
        cycle_id=row.cycle_id,
        now=_aware(row.decision_created_at),
    )
    return DecisionRecord(
        id=DecisionRecordId.parse(row.id),
        organization_id=OrganizationId.parse(row.organization_id),
        company_id=CompanyId.parse(row.company_id),
        decision=decision,
        observation_digest=row.observation_digest,
        outcome_context=dict(row.outcome_context or {}),
        recorded_at=_aware(row.recorded_at),
    )


class SqlAlchemyDecisionRecordRepository:
    """Flush-only append-only store; callers own the transaction boundary."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: DecisionRecord) -> None:
        decision = record.decision
        decision.validate()
        self._session.add(
            DecisionRecordModel(
                id=str(record.id),
                organization_id=str(record.organization_id),
                company_id=str(record.company_id),
                decision_type=decision.decision_type.value,
                payload=dict(decision.payload),
                rationale=decision.rationale,
                confidence=decision.confidence,
                model_reference=decision.model_reference,
                cycle_id=decision.cycle_id,
                task_id=str(decision.task_id) if decision.task_id else None,
                decision_created_at=decision.created_at,
                observation_digest=record.observation_digest,
                outcome_context=dict(record.outcome_context),
                recorded_at=record.recorded_at,
            )
        )
        await self._session.flush()

    async def list_for_company(
        self,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        limit: int = 100,
    ) -> list[DecisionRecord]:
        stmt = (
            select(DecisionRecordModel)
            .where(
                DecisionRecordModel.organization_id == str(organization_id),
                DecisionRecordModel.company_id == str(company_id),
            )
            .order_by(DecisionRecordModel.recorded_at.desc(), DecisionRecordModel.id.desc())
            .limit(max(1, min(limit, 1000)))
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_record(row) for row in rows]
