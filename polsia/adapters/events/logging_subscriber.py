"""Default observer: logs every domain event it receives."""

from __future__ import annotations

from polsia.core.shared.events import EventEnvelope
from polsia.infra.logging import get_logger


class LoggingSubscriber:
    """Subscribes to ``"*"`` and logs each envelope as a structured info event."""

    def __init__(self) -> None:
        self._log = get_logger(__name__)

    def __call__(self, event: EventEnvelope) -> None:
        self._log.info(
            "domain_event",
            type=event.type,
            id=str(event.id),
            organization_id=str(event.organization_id) if event.organization_id else None,
            company_id=str(event.company_id) if event.company_id else None,
            payload=event.payload,
        )
