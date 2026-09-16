"""Event envelope contract and the EventBus port.

This is the *contract* for all Polsia domain events (ADR-0004). The envelope
is stable: an in-process bus ships in Phase 1 and a durable bus may replace
it in Phase 2 without changing services or consumers.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from polsia.core.shared.ids import CompanyId, EventId, OrganizationId


def utcnow() -> dt.datetime:
    """Timezone-aware UTC now (single clock for the whole OS)."""
    return dt.datetime.now(dt.UTC)


def _type_name(event_type: str) -> str:
    parts = event_type.split(".")
    if len(parts) != 2 or not all(p and p.replace("_", "").isalpha() for p in parts):
        raise ValueError(
            f"event type must be '<namespace>.<name>' in snake case, got {event_type!r}"
        )
    return event_type


class EventEnvelope:
    """Immutable domain event envelope.

    Attributes:
        id: unique event id (`evt_…`).
        type: namespaced snake-case type, e.g. ``company.created``.
        occurred_at: timezone-aware UTC timestamp.
        organization_id: tenant scope (may be None for platform events).
        company_id: company scope, if the event belongs to a company.
        payload: event-specific data (JSON-serializable, no business schema).
    """

    __slots__ = ("id", "type", "occurred_at", "organization_id", "company_id", "payload")

    def __init__(
        self,
        *,
        event_type: str,
        organization_id: OrganizationId | None = None,
        company_id: CompanyId | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: dt.datetime | None = None,
        event_id: EventId | None = None,
    ) -> None:
        _type_name(event_type)
        if occurred_at is not None and occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "id", event_id or EventId.generate())
        object.__setattr__(self, "type", event_type)
        object.__setattr__(self, "occurred_at", occurred_at or utcnow())
        object.__setattr__(self, "organization_id", organization_id)
        object.__setattr__(self, "company_id", company_id)
        object.__setattr__(self, "payload", dict(payload or {}))

    def __setattr__(self, name: str, value: Any) -> None:  # pragma: no cover
        raise AttributeError("EventEnvelope is immutable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "type": self.type,
            "occurred_at": self.occurred_at.isoformat(),
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "company_id": str(self.company_id) if self.company_id else None,
            "payload": self.payload,
        }


EventHandler = Callable[[EventEnvelope], None]
AsyncEventHandler = Callable[[EventEnvelope], Awaitable[None]]


class EventBus(Protocol):
    """Port for publishing and subscribing to domain events.

    Consumers subscribe by event *type prefix* (namespace, e.g.
    ``"company"``) or the wildcard ``"*"``. Publishers must not assume
    synchronous delivery (Phase 2 bus is at-least-once and possibly async).
    """

    def publish(self, event: EventEnvelope) -> None: ...

    def subscribe(self, pattern: str, handler: EventHandler) -> None: ...
