"""In-process, synchronous EventBus adapter.

Phase 1 implementation of the ``EventBus`` port: dispatch happens on the
publisher's thread, immediately. A durable bus may replace this in Phase 2
without changing the port or its consumers.
"""

from __future__ import annotations

import threading

from polsia.core.shared.events import EventEnvelope, EventHandler
from polsia.infra.logging import get_logger

_WILDCARD = "*"


class InProcessEventBus:
    """Synchronous in-memory event bus.

    Patterns:
        - exact event type: ``"company.created"``
        - namespace prefix: ``"company."`` matches every ``company.*`` type
        - wildcard: ``"*"`` matches everything

    Handler exceptions are logged (``event="event_handler_error"``) and
    swallowed so one failing subscriber cannot break the publisher or the
    remaining handlers. Dispatch iterates a snapshot taken under a lock, so
    subscribing while publishing is safe.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscriptions: dict[str, list[EventHandler]] = {}

    def subscribe(self, pattern: str, handler: EventHandler) -> None:
        """Register ``handler`` for an exact type, namespace, or ``"*"``."""
        if not pattern:
            raise ValueError("pattern must be non-empty")
        with self._lock:
            self._subscriptions.setdefault(pattern, []).append(handler)

    def publish(self, event: EventEnvelope) -> None:
        """Dispatch ``event`` synchronously to all matching handlers.

        Never raises due to handler errors; publishing to zero subscribers
        is a no-op.
        """
        handlers = self._matching_handlers(event.type)
        log = get_logger(__name__)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                log.exception(
                    "event_handler_error",
                    event_id=str(event.id),
                    event_type=event.type,
                    handler=getattr(handler, "__qualname__", repr(handler)),
                )

    def subscriber_count(self, pattern: str) -> int:
        """Number of handlers registered for ``pattern`` (test introspection)."""
        with self._lock:
            return len(self._subscriptions.get(pattern, []))

    def _matching_handlers(self, event_type: str) -> list[EventHandler]:
        with self._lock:
            handlers: list[EventHandler] = []
            for pattern, subscribed in self._subscriptions.items():
                if pattern == _WILDCARD or pattern == event_type or event_type.startswith(pattern):
                    handlers.extend(subscribed)
            return handlers
