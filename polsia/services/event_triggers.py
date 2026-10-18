"""Internal durable event-trigger dispatcher with at-most-once cycle claims."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.event_models import EventRecordModel
from polsia.adapters.persistence.repositories import (
    SqlAlchemyCompanyRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from polsia.adapters.persistence.trigger_models import EventTriggerPolicyModel, TriggerDeliveryModel
from polsia.adapters.persistence.trigger_repositories import (
    SqlAlchemyEventTriggerPolicyRepository,
    SqlAlchemyStoredEventRepository,
    SqlAlchemyTriggerDeliveryRepository,
)
from polsia.core.ceo.triggers import (
    DELIVERY_COMPLETED,
    DELIVERY_FAILED,
    DELIVERY_INTERRUPTED,
    DELIVERY_RECURSION_SUPPRESSED,
    DELIVERY_REJECTED,
    TRIGGER_EVENT_TYPES,
    EventTriggerPolicy,
)
from polsia.core.shared.errors import PolsiaError
from polsia.core.shared.events import EventEnvelope, event_metadata_scope, utcnow
from polsia.core.shared.ids import EventId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.values import CompanyStatus, MembershipRole, OrganizationStatus


class SessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


RunTriggeredCycle = Callable[
    [User, EventTriggerPolicy, EventEnvelope, str], Awaitable[dict[str, Any]]
]


def trigger_cycle_id(policy_id: str, event_id: EventId) -> str:
    """Return the stable cycle key for one policy/envelope pair."""
    return f"trg_{uuid.uuid5(uuid.NAMESPACE_URL, policy_id + ':' + str(event_id)).hex[:32]}"


class EventTriggerDispatcher:
    """Receives allowlisted event notices and processes committed envelopes.

    The sync EventBus handler only schedules work. Every delivery reloads the
    durable envelope and acquires a leased, fenced claim in a fresh committed
    transaction before the supplied loop callback may cause side effects. If a
    worker stops without recording an outcome, the lease expires and a later
    claim recovers the pair with a higher fencing token; retries are bounded.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        run_cycle: RunTriggeredCycle,
        clock: Callable[[], dt.datetime] = utcnow,
        commit_wait_seconds: float = 2.0,
        catchup_limit: int = 1000,
        owner_id: str | None = None,
        lease_seconds: int = 30,
        max_delivery_attempts: int = 3,
    ) -> None:
        self._session_factory = session_factory
        self._run_cycle = run_cycle
        self._clock = clock
        self._commit_wait_seconds = max(0.0, min(commit_wait_seconds, 10.0))
        self._catchup_limit = max(1, min(catchup_limit, 1000))
        self._owner_id = owner_id or uuid.uuid4().hex
        self._lease_seconds = max(1, min(lease_seconds, 3600))
        self._max_delivery_attempts = max(1, min(max_delivery_attempts, 10))
        self._tasks: set[asyncio.Task[int]] = set()
        self._accepting = True

    def subscribe(self, bus: Any) -> None:
        for event_type in sorted(TRIGGER_EVENT_TYPES):
            bus.subscribe(event_type, self.on_event)

    def stop_accepting(self) -> None:
        """Prevent new bus notices from creating work during shutdown."""
        self._accepting = False

    def on_event(self, notice: EventEnvelope) -> None:
        if not self._accepting or notice.type not in TRIGGER_EVENT_TYPES:
            return
        try:
            task = asyncio.get_running_loop().create_task(self.dispatch(notice.id))
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for all scheduled deliveries after closing the notice gate."""
        self.stop_accepting()
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def catch_up(self) -> int:
        """Replay eligible envelopes, including expired/interrupted leases.

        Bounds the scan; a pair with a live lease, a terminal outcome, or an
        exhausted attempt budget is excluded. Terminal rows therefore never
        consume the catch-up budget.
        """
        now = self._clock()
        max_attempts = self._max_delivery_attempts
        async with self._session_factory() as session:
            stmt = (
                select(EventRecordModel.id)
                .join(
                    EventTriggerPolicyModel,
                    (EventTriggerPolicyModel.organization_id == EventRecordModel.organization_id)
                    & (EventTriggerPolicyModel.company_id == EventRecordModel.company_id)
                    & (EventTriggerPolicyModel.event_type == EventRecordModel.event_type),
                )
                .outerjoin(
                    TriggerDeliveryModel,
                    (TriggerDeliveryModel.policy_id == EventTriggerPolicyModel.id)
                    & (TriggerDeliveryModel.event_id == EventRecordModel.id),
                )
                .where(
                    EventTriggerPolicyModel.enabled.is_(True),
                    EventRecordModel.event_type.in_(TRIGGER_EVENT_TYPES),
                    or_(
                        TriggerDeliveryModel.id.is_(None),
                        and_(
                            TriggerDeliveryModel.status == DELIVERY_INTERRUPTED,
                            TriggerDeliveryModel.attempts < max_attempts,
                        ),
                        and_(
                            TriggerDeliveryModel.status == "claimed",
                            or_(
                                TriggerDeliveryModel.lease_expires_at.is_(None),
                                TriggerDeliveryModel.lease_expires_at <= now,
                            ),
                            TriggerDeliveryModel.attempts < max_attempts,
                        ),
                    ),
                )
                .order_by(EventRecordModel.occurred_at, EventRecordModel.id)
                .limit(self._catchup_limit)
            )
            event_ids = list(
                dict.fromkeys(EventId.parse(value) for value in (await session.scalars(stmt)).all())
            )
            await session.rollback()
        count = 0
        for event_id in dict.fromkeys(event_ids):
            count += await self.dispatch(event_id)
        return count

    async def dispatch(self, event_id: EventId) -> int:
        delay = 0.01
        deadline = time.monotonic() + self._commit_wait_seconds
        event: EventEnvelope | None = None
        policies: list[EventTriggerPolicy] = []
        while True:
            async with self._session_factory() as session:
                event = await SqlAlchemyStoredEventRepository(session).get(event_id)
                if event is not None:
                    if event.organization_id is None or event.company_id is None:
                        return 0
                    policies = await SqlAlchemyEventTriggerPolicyRepository(session).list_matching(
                        event.organization_id, event.company_id, event.type
                    )
                    break
            if time.monotonic() >= deadline:
                return 0
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.25)
        if event is None:
            return 0
        delivered = 0
        recursive = any(
            key in event.metadata
            for key in ("trigger_event_id", "trigger_policy_id", "trigger_cycle_id")
        )
        for policy in policies:
            cycle_id = trigger_cycle_id(policy.id, event.id)
            now = self._clock()
            async with self._session_factory() as session:
                deliveries = SqlAlchemyTriggerDeliveryRepository(session)
                lease = await deliveries.claim(
                    policy,
                    event.id,
                    cycle_id,
                    owner=self._owner_id,
                    lease_seconds=self._lease_seconds,
                    max_attempts=self._max_delivery_attempts,
                    now=now,
                )
                if lease is None:
                    await session.rollback()
                    continue
                await session.commit()
            delivered += 1
            if recursive:
                await self._finish(
                    policy,
                    event,
                    DELIVERY_RECURSION_SUPPRESSED,
                    {
                        "cycle_id": cycle_id,
                        "trigger_event_id": str(event.metadata.get("trigger_event_id", "")),
                        "trigger_policy_id": str(event.metadata.get("trigger_policy_id", "")),
                        "trigger_cycle_id": str(event.metadata.get("trigger_cycle_id", "")),
                    },
                    fencing_token=lease.fencing_token,
                    attempt=lease.attempt,
                )
                continue
            if event.type not in TRIGGER_EVENT_TYPES or event.type != policy.event_type:
                await self._finish(
                    policy,
                    event,
                    DELIVERY_REJECTED,
                    {"cycle_id": cycle_id, "reason": "rejected_event_type"},
                    fencing_token=lease.fencing_token,
                    attempt=lease.attempt,
                )
                continue
            if (
                event.organization_id != policy.organization_id
                or event.company_id != policy.company_id
            ):
                await self._finish(
                    policy,
                    event,
                    DELIVERY_REJECTED,
                    {"cycle_id": cycle_id, "reason": "tenant_mismatch"},
                    fencing_token=lease.fencing_token,
                    attempt=lease.attempt,
                )
                continue
            try:
                async with self._session_factory() as session:
                    company = await SqlAlchemyCompanyRepository(session).get(
                        policy.organization_id, policy.company_id
                    )
                    organization = await SqlAlchemyOrganizationRepository(session).get(
                        policy.organization_id
                    )
                    if (
                        company is None
                        or organization is None
                        or organization.status is not OrganizationStatus.ACTIVE
                        or company.status is not CompanyStatus.ACTIVE
                    ):
                        raise ValueError("trigger tenant no longer exists or is inactive")
                    membership = await SqlAlchemyMembershipRepository(session).get(
                        policy.organization_id, organization.owner_user_id
                    )
                    actor = await SqlAlchemyUserRepository(session).get(organization.owner_user_id)
                    if (
                        membership is None
                        or actor is None
                        or not actor.is_active()
                        or membership.role is not MembershipRole.OWNER
                    ):
                        raise ValueError("active organization owner membership required")
                    fresh_policies = await SqlAlchemyEventTriggerPolicyRepository(
                        session
                    ).list_matching(policy.organization_id, policy.company_id, event.type)
                    fresh_policy = next(
                        (item for item in fresh_policies if item.id == policy.id), None
                    )
                    if fresh_policy is None:
                        raise ValueError("trigger policy was disabled or mismatched")
                    policy = fresh_policy
                if actor is None:
                    raise ValueError("active organization owner membership required")
                with event_metadata_scope(
                    {
                        "trigger_event_id": str(event.id),
                        "trigger_policy_id": policy.id,
                        "trigger_cycle_id": cycle_id,
                    }
                ):
                    result = await self._run_cycle(actor, policy, event, cycle_id)
                await self._finish(
                    policy,
                    event,
                    DELIVERY_COMPLETED,
                    {**result, "cycle_id": cycle_id},
                    fencing_token=lease.fencing_token,
                    attempt=lease.attempt,
                )
            except Exception as exc:
                deterministic = isinstance(exc, (PolsiaError, ValueError, PermissionError))
                if deterministic:
                    status = DELIVERY_REJECTED
                elif lease.attempt < self._max_delivery_attempts:
                    status = DELIVERY_INTERRUPTED
                else:
                    status = DELIVERY_FAILED
                await self._finish(
                    policy,
                    event,
                    status,
                    {"cycle_id": cycle_id, "error": type(exc).__name__},
                    fencing_token=lease.fencing_token,
                    attempt=lease.attempt,
                )
        return delivered

    async def _finish(
        self,
        policy: EventTriggerPolicy,
        event: EventEnvelope,
        status: str,
        detail: dict[str, Any],
        *,
        fencing_token: int,
        attempt: int,
    ) -> None:
        async with self._session_factory() as session:
            await SqlAlchemyTriggerDeliveryRepository(session).finish(
                policy.id,
                event.id,
                status,
                {**detail, "attempt": attempt},
                fencing_token=fencing_token,
                now=self._clock(),
            )
            await session.commit()
