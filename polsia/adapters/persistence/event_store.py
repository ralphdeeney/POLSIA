"""SQLAlchemy implementation of the EventStore port (Phase 2, ADR-0005).

Durable persistence for domain events. ``append`` is flush-only: it
participates in the caller's transaction, so a rolled-back mutation leaves no
event behind (transactional-outbox-consistent). Retrieval is tenant-scoped.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.event_models import EventRecordModel
from polsia.core.shared.events import EventEnvelope
from polsia.core.shared.ids import CompanyId, EventId, OrganizationId


def _aware(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


def _to_envelope(row: EventRecordModel) -> EventEnvelope:
    return EventEnvelope(
        event_type=row.event_type,
        organization_id=OrganizationId.parse(row.organization_id) if row.organization_id else None,
        company_id=CompanyId.parse(row.company_id) if row.company_id else None,
        payload=dict(row.payload or {}),
        occurred_at=_aware(row.occurred_at),
        event_id=EventId.parse(row.id),
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        actor=row.actor,
        metadata=dict(row.metadata_json or {}),
    )


class SqlAlchemyEventStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: EventEnvelope) -> None:
        row = EventRecordModel(
            id=str(event.id),
            event_type=event.type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            organization_id=str(event.organization_id) if event.organization_id else None,
            company_id=str(event.company_id) if event.company_id else None,
            occurred_at=event.occurred_at,
            actor=event.actor,
            payload=dict(event.payload),
            metadata_json=dict(event.metadata),
        )
        self._session.add(row)
        await self._session.flush()

    async def list_for_organization(
        self, organization_id: OrganizationId, *, limit: int = 100
    ) -> list[EventEnvelope]:
        stmt = (
            select(EventRecordModel)
            .where(EventRecordModel.organization_id == str(organization_id))
            .order_by(EventRecordModel.occurred_at.desc(), EventRecordModel.id.desc())
            .limit(max(1, min(limit, 1000)))
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_envelope(row) for row in rows]

    async def list_for_company(
        self, organization_id: OrganizationId, company_id: CompanyId, *, limit: int = 100
    ) -> list[EventEnvelope]:
        stmt = (
            select(EventRecordModel)
            .where(
                EventRecordModel.organization_id == str(organization_id),
                EventRecordModel.company_id == str(company_id),
            )
            .order_by(EventRecordModel.occurred_at.desc(), EventRecordModel.id.desc())
            .limit(max(1, min(limit, 1000)))
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_envelope(row) for row in rows]
