"""Transactional adapter for one authorized event-trigger cycle."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.trigger_repositories import SqlAlchemyTriggerDeliveryRepository
from polsia.core.ceo.triggers import EventTriggerPolicy
from polsia.core.permissions.errors import ApprovalRequired
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import EventEnvelope
from polsia.core.tenancy.entities import User
from polsia.services.autonomous_loop import AutonomousLoop


class SessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


class TriggeredLoopRunner:
    """Create a session-scoped loop per claimed delivery and own its transaction."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        loop_factory: Callable[[AsyncSession], AutonomousLoop],
    ) -> None:
        self._session_factory = session_factory
        self._loop_factory = loop_factory

    async def __call__(
        self,
        actor: User,
        policy: EventTriggerPolicy,
        event: EventEnvelope,
        cycle_id: str,
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            delivery = await SqlAlchemyTriggerDeliveryRepository(session).get_claim(
                policy.id, event.id
            )
            if (
                delivery is None
                or delivery.status != "claimed"
                or delivery.organization_id != str(policy.organization_id)
                or delivery.company_id != str(policy.company_id)
                or delivery.cycle_id != cycle_id
            ):
                raise ValidationError("matching committed trigger delivery claim required")
            loop = self._loop_factory(session)
            try:
                result = await loop.run_triggered_cycle(actor, policy, event, cycle_id)
            except ApprovalRequired:
                # Approval is an intentional terminal outcome for this delivery.
                await session.commit()
                raise
            except Exception:
                await session.rollback()
                raise
            await session.commit()
            return result
