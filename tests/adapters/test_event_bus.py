"""Tests for the in-process EventBus adapter and LoggingSubscriber."""

from __future__ import annotations

import pytest
from polsia.adapters.events import InProcessEventBus, LoggingSubscriber
from polsia.core.shared.events import EventEnvelope
from polsia.core.shared.ids import CompanyId, OrganizationId
from structlog.testing import capture_logs


def make_event(event_type: str) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        organization_id=OrganizationId.generate(),
        company_id=CompanyId.generate(),
        payload={"sample": True},
    )


class TestDispatchSemantics:
    def test_exact_type_subscription_receives_only_that_type(self) -> None:
        bus = InProcessEventBus()
        received: list[EventEnvelope] = []
        bus.subscribe("company.created", received.append)

        bus.publish(make_event("company.status_changed"))
        assert received == []

        event = make_event("company.created")
        bus.publish(event)
        assert received == [event]

    def test_namespace_pattern_receives_all_company_events(self) -> None:
        bus = InProcessEventBus()
        received: list[EventEnvelope] = []
        bus.subscribe("company.", received.append)

        bus.publish(make_event("user.registered"))
        assert received == []

        created = make_event("company.created")
        changed = make_event("company.status_changed")
        bus.publish(created)
        bus.publish(changed)
        assert received == [created, changed]

    def test_wildcard_receives_everything(self) -> None:
        bus = InProcessEventBus()
        received: list[EventEnvelope] = []
        bus.subscribe("*", received.append)

        events = [make_event("company.created"), make_event("user.registered")]
        for event in events:
            bus.publish(event)
        assert received == events

    def test_no_subscribers_publish_is_noop(self) -> None:
        bus = InProcessEventBus()
        bus.publish(make_event("company.created"))  # must not raise
        assert bus.subscriber_count("*") == 0

    def test_subscriber_count_reports_registrations(self) -> None:
        bus = InProcessEventBus()
        assert bus.subscriber_count("company.created") == 0
        bus.subscribe("company.created", lambda event: None)
        bus.subscribe("company.created", lambda event: None)
        assert bus.subscriber_count("company.created") == 2
        assert bus.subscriber_count("company.") == 0


class TestErrorIsolation:
    def test_failing_handler_does_not_break_others_or_publisher(self) -> None:
        bus = InProcessEventBus()
        calls: list[str] = []

        def boom(event: EventEnvelope) -> None:
            raise RuntimeError("subscriber exploded")

        bus.subscribe("company.created", boom)
        bus.subscribe("company.created", lambda event: calls.append(event.type))

        with capture_logs() as logs:
            bus.publish(make_event("company.created"))  # must not raise

        assert calls == ["company.created"]
        errors = [entry for entry in logs if entry["event"] == "event_handler_error"]
        assert len(errors) == 1
        assert errors[0]["event_id"]  # envelope id is present (ulid string)
        assert errors[0]["event_type"] == "company.created"

    def test_failing_handler_does_not_stop_later_namespace_handlers(self) -> None:
        bus = InProcessEventBus()
        calls: list[str] = []

        bus.subscribe("company.", lambda event: calls.append("first"))
        bus.subscribe("company.", lambda event: 1 / 0)  # type: ignore[arg-type]
        bus.subscribe("*", lambda event: calls.append("wildcard"))

        with capture_logs():
            bus.publish(make_event("company.created"))

        assert calls == ["first", "wildcard"]


class TestEventEnvelopeImmutability:
    def test_envelope_rejects_attribute_assignment(self) -> None:
        event = make_event("company.created")
        with pytest.raises(AttributeError, match="immutable"):
            event.type = "company.status_changed"  # type: ignore[misc]


class TestLoggingSubscriber:
    def test_logs_structured_domain_event(self) -> None:
        subscriber = LoggingSubscriber()
        event = make_event("company.status_changed")

        with capture_logs() as logs:
            subscriber(event)

        assert len(logs) == 1
        entry = logs[0]
        assert entry["event"] == "domain_event"
        assert entry["type"] == "company.status_changed"
        assert entry["id"] == str(event.id)
        assert entry["organization_id"] == str(event.organization_id)
        assert entry["company_id"] == str(event.company_id)
        assert entry["payload"] == {"sample": True}

    def test_bus_dispatches_to_logging_subscriber_via_wildcard(self) -> None:
        bus = InProcessEventBus()
        bus.subscribe("*", LoggingSubscriber())
        event = make_event("user.registered")

        with capture_logs() as logs:
            bus.publish(event)

        domain_events = [entry for entry in logs if entry["event"] == "domain_event"]
        assert len(domain_events) == 1
        assert domain_events[0]["type"] == "user.registered"
