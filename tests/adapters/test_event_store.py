"""Durable event store tests (Phase 2 slice 7, approved decision C1a)."""

from __future__ import annotations

from typing import Any

import pytest
from polsia.adapters.events import InProcessEventBus
from polsia.adapters.persistence.event_store import SqlAlchemyEventStore
from polsia.core.shared.events import EventEnvelope, emit
from polsia.core.shared.ids import CompanyId, EventId, OrganizationId
from polsia.infra.db import create_all_metadata
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def store_env():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            yield SqlAlchemyEventStore(session), session
    finally:
        await engine.dispose()


def make_envelope(**kwargs: object) -> EventEnvelope:
    return EventEnvelope(
        event_type="company.created",  # type: ignore[arg-type]
        organization_id=OrganizationId.generate(),
        company_id=CompanyId.generate(),
        payload={"name": "Test Co"},
        **kwargs,  # type: ignore[arg-type]
    )


class TestEventStorePersistence:
    async def test_append_and_list_roundtrip(self, store_env: Any) -> None:
        store, _session = store_env
        event = make_envelope(
            aggregate_type="company",
            aggregate_id="cmp_x",
            actor="agt_worker",
            metadata={"source": "test"},
        )
        await store.append(event)
        events = await store.list_for_organization(event.organization_id)
        assert len(events) == 1
        loaded = events[0]
        assert loaded.type == "company.created"
        assert loaded.id == event.id
        assert loaded.payload == {"name": "Test Co"}
        assert loaded.aggregate_type == "company"
        assert loaded.aggregate_id == "cmp_x"
        assert loaded.actor == "agt_worker"
        assert loaded.metadata == {"source": "test"}

    async def test_company_scoped_retrieval(self, store_env: Any) -> None:
        store, _session = store_env
        e1 = make_envelope()
        e2 = make_envelope()
        await store.append(e1)
        await store.append(e2)
        org_events = await store.list_for_organization(e1.organization_id)
        assert len(org_events) == 1
        company_events = await store.list_for_company(
            e1.organization_id,
            e1.company_id,  # type: ignore[arg-type]
        )
        assert len(company_events) == 1
        # cross-tenant retrieval returns nothing (isolation)
        other = await store.list_for_company(
            OrganizationId.generate(),
            e1.company_id,  # type: ignore[arg-type]
        )
        assert other == []

    async def test_new_envelope_fields_are_backward_compatible(self) -> None:
        """Phase 0-1 envelope construction (no new kwargs) still works, and
        to_dict() keeps all existing keys."""
        event = make_envelope()
        d = event.to_dict()
        for key in ("id", "type", "occurred_at", "organization_id", "company_id", "payload"):
            assert key in d
        assert event.aggregate_type is None
        assert event.aggregate_id is None
        assert event.actor is None
        assert event.metadata == {}

    async def test_emit_persists_then_dispatches(self, store_env: Any) -> None:
        """The shared publish path: mutation -> persist -> dispatch."""
        store, _session = store_env
        bus = InProcessEventBus()
        received: list[EventEnvelope] = []
        bus.subscribe("company.", received.append)
        event = make_envelope()
        await emit(bus, store, event)
        assert received == [event]
        stored = await store.list_for_organization(event.organization_id)
        assert [e.id for e in stored] == [event.id]

    async def test_emit_without_store_is_plain_dispatch(self) -> None:
        """Phase 0-1 style: store=None -> synchronous dispatch only."""
        bus = InProcessEventBus()
        received: list[EventEnvelope] = []
        bus.subscribe("*", received.append)
        event = make_envelope()
        await emit(bus, None, event)
        assert received == [event]

    async def test_emit_persistence_failure_aborts(self, store_env: Any) -> None:
        """A store failure propagates: the mutation must abort (no silent
        event loss in a transactional system)."""
        store, _session = store_env

        class FailingStore:
            async def append(self, event: EventEnvelope) -> None:
                raise RuntimeError("db down")

            async def list_for_organization(
                self, organization_id: OrganizationId, *, limit: int = 100
            ) -> list[EventEnvelope]:
                return []

            async def list_for_company(
                self, organization_id: OrganizationId, company_id: CompanyId, *, limit: int = 100
            ) -> list[EventEnvelope]:
                return []

        bus = InProcessEventBus()
        received: list[EventEnvelope] = []
        bus.subscribe("*", received.append)
        with pytest.raises(RuntimeError):
            await emit(bus, FailingStore(), make_envelope())  # type: ignore[arg-type]
        assert received == []  # dispatch never happened: persist comes first
        _ = store

    async def test_event_ids_are_typed(self, store_env: Any) -> None:
        event = make_envelope()
        assert isinstance(event.id, EventId)
        assert str(event.id).startswith("evt_")
        _store = store_env[0]
