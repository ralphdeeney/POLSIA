"""Persistence adapters for trigger policies, deliveries, and event lookup."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.event_models import EventRecordModel
from polsia.adapters.persistence.trigger_models import EventTriggerPolicyModel, TriggerDeliveryModel
from polsia.core.ceo.triggers import (
    DELIVERY_TERMINAL_STATUSES,
    DeliveryLease,
    EventTriggerPolicy,
    SafetyLimits,
)
from polsia.core.shared.events import EventEnvelope
from polsia.core.shared.ids import (
    CompanyId,
    EventId,
    OrganizationId,
    TriggerDeliveryId,
    TriggerPolicyId,
)


def _aware(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


def _to_policy(row: EventTriggerPolicyModel) -> EventTriggerPolicy:
    limits = SafetyLimits(**dict(row.limits or {}))
    return EventTriggerPolicy(
        id=TriggerPolicyId.parse(row.id),
        organization_id=OrganizationId.parse(row.organization_id),
        company_id=CompanyId.parse(row.company_id),
        event_type=row.event_type,
        enabled=row.enabled,
        limits=limits,
        created_at=_aware(row.created_at),
    )


def _to_event(row: EventRecordModel) -> EventEnvelope:
    if row.organization_id is None or row.company_id is None:
        return EventEnvelope(
            event_type=row.event_type,
            payload=dict(row.payload or {}),
            occurred_at=_aware(row.occurred_at),
            event_id=EventId.parse(row.id),
            aggregate_type=row.aggregate_type,
            aggregate_id=row.aggregate_id,
            actor=row.actor,
            metadata=dict(row.metadata_json or {}),
        )
    return EventEnvelope(
        event_type=row.event_type,
        organization_id=OrganizationId.parse(row.organization_id),
        company_id=CompanyId.parse(row.company_id),
        payload=dict(row.payload or {}),
        occurred_at=_aware(row.occurred_at),
        event_id=EventId.parse(row.id),
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        actor=row.actor,
        metadata=dict(row.metadata_json or {}),
    )


class SqlAlchemyEventTriggerPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, policy: EventTriggerPolicy) -> None:
        self._session.add(
            EventTriggerPolicyModel(
                id=policy.id,
                organization_id=str(policy.organization_id),
                company_id=str(policy.company_id),
                event_type=policy.event_type,
                enabled=policy.enabled,
                limits=json.loads(json.dumps(dataclasses.asdict(policy.limits))),
                created_at=policy.created_at,
            )
        )
        await self._session.flush()

    async def list_matching(
        self, organization_id: OrganizationId, company_id: CompanyId, event_type: str
    ) -> list[EventTriggerPolicy]:
        stmt = select(EventTriggerPolicyModel).where(
            EventTriggerPolicyModel.organization_id == str(organization_id),
            EventTriggerPolicyModel.company_id == str(company_id),
            EventTriggerPolicyModel.event_type == event_type,
            EventTriggerPolicyModel.enabled.is_(True),
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_policy(row) for row in rows]


class SqlAlchemyTriggerDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
        self,
        policy: EventTriggerPolicy,
        event_id: EventId,
        cycle_id: str,
        *,
        owner: str,
        lease_seconds: int,
        max_attempts: int,
        now: dt.datetime,
    ) -> DeliveryLease | None:
        """Grant a leased, fenced claim, or return ``None`` if not claimable.

        The first claim inserts the unique ``(policy_id, event_id)`` row. Later
        claims take over only a retryable (``interrupted``) row or a ``claimed``
        row whose lease has expired, while attempts remain. Every takeover
        increments ``fencing_token`` so a stale worker cannot record its result.
        """
        expires_at = now + dt.timedelta(seconds=lease_seconds)
        row = TriggerDeliveryModel(
            id=TriggerDeliveryId.generate(),
            policy_id=policy.id,
            event_id=str(event_id),
            organization_id=str(policy.organization_id),
            company_id=str(policy.company_id),
            cycle_id=cycle_id,
            status="claimed",
            detail={},
            attempts=1,
            lease_owner=owner,
            lease_expires_at=expires_at,
            fencing_token=1,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
        except Exception:
            await self._session.rollback()
            raise
        else:
            return DeliveryLease(fencing_token=1, attempt=1)

        existing: TriggerDeliveryModel | None = await self._session.scalar(
            select(TriggerDeliveryModel)
            .where(
                TriggerDeliveryModel.policy_id == policy.id,
                TriggerDeliveryModel.event_id == str(event_id),
            )
            .with_for_update()
        )
        if existing is None:
            return None
        if existing.status in DELIVERY_TERMINAL_STATUSES or existing.attempts >= max_attempts:
            return None
        lease_expiry = (
            _aware(existing.lease_expires_at) if existing.lease_expires_at is not None else None
        )
        if existing.status == "claimed" and lease_expiry is not None and lease_expiry > now:
            return None
        existing.attempts += 1
        existing.fencing_token += 1
        existing.lease_owner = owner
        existing.lease_expires_at = expires_at
        existing.status = "claimed"
        existing.detail = {}
        await self._session.flush()
        return DeliveryLease(fencing_token=existing.fencing_token, attempt=existing.attempts)

    async def get_claim(self, policy_id: str, event_id: EventId) -> TriggerDeliveryModel | None:
        row: TriggerDeliveryModel | None = await self._session.scalar(
            select(TriggerDeliveryModel).where(
                TriggerDeliveryModel.policy_id == policy_id,
                TriggerDeliveryModel.event_id == str(event_id),
            )
        )
        return row

    async def finish(
        self,
        policy_id: str,
        event_id: EventId,
        status: str,
        detail: dict[str, Any],
        *,
        fencing_token: int,
        now: dt.datetime,
    ) -> bool:
        """Record an outcome, fenced on the lease token.

        Returns ``False`` when the token no longer matches (a stale worker lost
        its lease to a newer claim); the newer owner's row is left untouched.
        """
        result = await self._session.execute(
            update(TriggerDeliveryModel)
            .where(
                TriggerDeliveryModel.policy_id == policy_id,
                TriggerDeliveryModel.event_id == str(event_id),
                TriggerDeliveryModel.fencing_token == fencing_token,
            )
            .values(
                status=status,
                detail=json.loads(json.dumps(detail, allow_nan=False)),
                finished_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return isinstance(result, CursorResult) and result.rowcount > 0


class SqlAlchemyStoredEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, event_id: EventId) -> EventEnvelope | None:
        row = await self._session.get(EventRecordModel, str(event_id))
        return _to_event(row) if row is not None else None
