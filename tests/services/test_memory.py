"""MemoryService tests with an in-memory fake MemoryPort.

Covers write/read/search/remove happy paths, event emission (types +
payloads), scope filtering, and tenant isolation (search is always
company-scoped — never leaks other tenants).
"""

from __future__ import annotations

import pytest
from polsia.core.memory.entities import MemoryRecord
from polsia.core.memory.ports import MemoryPort
from polsia.core.shared.errors import (
    CompanyNotFoundError,
    NotAMember,
    NotFoundError,
    ValidationError,
)
from polsia.core.shared.events import EventEnvelope, EventHandler
from polsia.core.shared.ids import CompanyId, MemoryRecordId, OrganizationId
from polsia.core.tenancy.entities import Company, OrgMembership, User
from polsia.core.tenancy.values import MembershipRole
from polsia.services.memory import MemoryService

# -- In-memory fakes -----------------------------------------------------------


class RecordingBus:
    """Fake EventBus: records every published event and dispatches to
    subscribers matching the type prefix (or the wildcard "*")."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []
        self._handlers: list[tuple[str, EventHandler]] = []

    def subscribe(self, pattern: str, handler: EventHandler) -> None:
        self._handlers.append((pattern, handler))

    def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)
        for pattern, handler in self._handlers:
            if pattern == "*" or event.type.startswith(pattern):
                handler(event)


class FakeMemoryStore:
    """In-memory implementation of the MemoryPort for service tests."""

    def __init__(self) -> None:
        self.by_id: dict[str, MemoryRecord] = {}

    async def store(self, record: MemoryRecord) -> None:
        self.by_id[str(record.id)] = record

    async def retrieve(
        self, company_id: CompanyId, record_id: MemoryRecordId
    ) -> MemoryRecord | None:
        record = self.by_id.get(str(record_id))
        if record is not None and record.company_id == company_id:
            return record
        return None

    async def search(
        self, company_id: CompanyId, query: str, *, scope: str | None = None, limit: int = 20
    ) -> list[MemoryRecord]:
        terms = [term.lower() for term in query.split() if term]
        if not terms:
            return []
        results = [
            record
            for record in self.by_id.values()
            if record.company_id == company_id
            and (scope is None or record.scope == scope)
            and all(term in record.content.lower() for term in terms)
        ]
        return results[: max(1, min(limit, 1000))]

    async def delete(self, company_id: CompanyId, record_id: MemoryRecordId) -> None:
        record = self.by_id.get(str(record_id))
        if record is not None and record.company_id == company_id:
            del self.by_id[str(record_id)]

    async def list(
        self, company_id: CompanyId, *, scope: str | None = None, limit: int = 100
    ) -> list[MemoryRecord]:
        results = [
            record
            for record in self.by_id.values()
            if record.company_id == company_id and (scope is None or record.scope == scope)
        ]
        return results[: max(1, min(limit, 1000))]


class FakeCompanyRepository:
    def __init__(self) -> None:
        self.by_id: dict[CompanyId, Company] = {}

    async def add(self, company: Company) -> None:
        self.by_id[company.id] = company

    async def get(self, organization_id: OrganizationId, company_id: CompanyId) -> Company | None:
        company = self.by_id.get(company_id)
        if company is not None and company.organization_id == organization_id:
            return company
        return None

    async def get_by_slug(self, organization_id: OrganizationId, slug: str) -> Company | None:
        for company in self.by_id.values():
            if company.organization_id == organization_id and company.slug == slug:
                return company
        return None

    async def list_for_organization(self, organization_id: OrganizationId) -> list[Company]:
        return [c for c in self.by_id.values() if c.organization_id == organization_id]

    async def update(self, company: Company) -> None:
        self.by_id[company.id] = company


class FakeMembershipRepository:
    def __init__(self) -> None:
        self.by_key: dict[tuple[OrganizationId, UserId], OrgMembership] = {}

    async def add(self, membership: OrgMembership) -> None:
        self.by_key[(membership.organization_id, membership.user_id)] = membership

    async def get(self, organization_id: OrganizationId, user_id: UserId) -> OrgMembership | None:
        return self.by_key.get((organization_id, user_id))

    async def list_for_organization(self, organization_id: OrganizationId) -> list[OrgMembership]:
        return [m for m in self.by_key.values() if m.organization_id == organization_id]

    async def update(self, membership: OrgMembership) -> None:
        self.by_key[(membership.organization_id, membership.user_id)] = membership


from polsia.core.shared.ids import UserId  # noqa: E402

# -- Fixtures / helpers ----------------------------------------------------------


class World:
    def __init__(self) -> None:
        self.bus = RecordingBus()
        self.memory = FakeMemoryStore()
        self.companies = FakeCompanyRepository()
        self.memberships = FakeMembershipRepository()
        self.service = MemoryService(
            memory=self.memory,
            companies=self.companies,
            memberships=self.memberships,
            bus=self.bus,
        )


async def make_member(world: World) -> tuple[User, OrganizationId, CompanyId]:
    user = User.register(email="owner@example.com", hashed_password="x", display_name="Owner")
    organization_id = OrganizationId.generate()
    company = Company.create(organization_id=organization_id, name="Co", slug="co-1")
    await world.companies.add(company)
    await world.memberships.add(
        OrgMembership.create(
            organization_id=organization_id, user_id=user.id, role=MembershipRole.OWNER
        )
    )
    return user, organization_id, company.id


def write_kwargs(**overrides: object) -> dict:
    kwargs: dict = {
        "scope": "goal",
        "type": "observation",
        "content": "Customer asked about invoicing.",
    }
    kwargs.update(overrides)
    return kwargs


# -- Write / read ---------------------------------------------------------------


class TestWrite:
    async def test_write_stores_and_emits_memory_created(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        record = await world.service.write(
            user, org_id, company_id, **write_kwargs(metadata={"k": "v"}, importance=0.8)
        )
        assert record.company_id == company_id
        assert record.scope == "goal"
        assert record.importance == 0.8
        stored = await world.memory.retrieve(company_id, record.id)
        assert stored is not None
        event = world.bus.events[-1]
        assert event.type == "memory.created"
        assert event.company_id == company_id
        assert event.payload["memory_record_id"] == str(record.id)
        assert event.payload["scope"] == "goal"
        assert event.payload["type"] == "observation"

    async def test_write_requires_member(self) -> None:
        world = World()
        outsider = User.register(email="out@example.com", hashed_password="x", display_name="Out")
        organization_id = OrganizationId.generate()
        company_id = CompanyId.generate()
        with pytest.raises(NotAMember):
            await world.service.write(outsider, organization_id, company_id, **write_kwargs())

    async def test_write_requires_company_in_organization(self) -> None:
        world = World()
        user, org_id, _ = await make_member(world)
        other_company = CompanyId.generate()
        with pytest.raises(CompanyNotFoundError):
            await world.service.write(user, org_id, other_company, **write_kwargs())

    async def test_write_validates_content(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        with pytest.raises(ValidationError):
            await world.service.write(
                user,
                org_id,
                company_id,
                **write_kwargs(content="   "),
            )


class TestRead:
    async def test_read_returns_stored_record(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        record = await world.service.write(user, org_id, company_id, **write_kwargs())
        loaded = await world.service.read(user, org_id, company_id, record.id)
        assert loaded.id == record.id
        assert loaded.content == record.content

    async def test_read_missing_record_404(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        with pytest.raises(NotFoundError):
            await world.service.read(user, org_id, company_id, MemoryRecordId.generate())

    async def test_read_requires_member(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        record = await world.service.write(user, org_id, company_id, **write_kwargs())
        outsider = User.register(email="out2@example.com", hashed_password="x", display_name="Out")
        with pytest.raises(NotAMember):
            await world.service.read(outsider, org_id, company_id, record.id)

    async def test_read_does_not_leak_other_tenant(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        record = await world.service.write(user, org_id, company_id, **write_kwargs())
        other_user, other_org, _ = await make_member(world)
        # The other tenant cannot reach this company at all (scope mismatch),
        # and never receives the record.
        with pytest.raises(CompanyNotFoundError):
            await world.service.read(other_user, other_org, company_id, record.id)


# -- Search ----------------------------------------------------------------------


class TestSearch:
    async def test_search_matches_keyword(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        await world.service.write(
            user, org_id, company_id, **write_kwargs(content="Customer asked about invoicing.")
        )
        await world.service.write(
            user, org_id, company_id, **write_kwargs(content="Team standup notes.")
        )
        results = await world.service.search(user, org_id, company_id, "invoicing")
        assert len(results) == 1
        assert results[0].content == "Customer asked about invoicing."

    async def test_search_is_case_insensitive(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        await world.service.write(
            user, org_id, company_id, **write_kwargs(content="Customer asked about INVOICING.")
        )
        results = await world.service.search(user, org_id, company_id, "invoicing")
        assert len(results) == 1

    async def test_search_scope_filter(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        await world.service.write(
            user,
            org_id,
            company_id,
            **write_kwargs(content="Invoicing question.", scope="goal"),
        )
        await world.service.write(
            user,
            org_id,
            company_id,
            **write_kwargs(content="Invoicing reminder.", scope="task"),
        )
        results = await world.service.search(user, org_id, company_id, "invoicing", scope="task")
        assert len(results) == 1
        assert results[0].scope == "task"

    async def test_search_tenant_isolation(self) -> None:
        world = World()
        user_a, org_a, company_a = await make_member(world)
        await world.service.write(
            user_a, org_a, company_a, **write_kwargs(content="Company A secret memory.")
        )
        user_b, org_b, company_b = await make_member(world)
        await world.service.write(
            user_b, org_b, company_b, **write_kwargs(content="Company B secret memory.")
        )
        # B searching company B never sees A's records.
        results_b = await world.service.search(user_b, org_b, company_b, "secret")
        assert len(results_b) == 1
        assert results_b[0].company_id == company_b
        # A searching company A never sees B's records.
        results_a = await world.service.search(user_a, org_a, company_a, "secret")
        assert len(results_a) == 1
        assert results_a[0].company_id == company_a
        assert all(r.content != "Company B secret memory." for r in results_a)

    async def test_search_requires_member(self) -> None:
        world = World()
        _, org_id, company_id = await make_member(world)
        outsider = User.register(email="out3@example.com", hashed_password="x", display_name="Out")
        with pytest.raises(NotAMember):
            await world.service.search(outsider, org_id, company_id, "anything")

    async def test_search_respects_limit(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        for i in range(5):
            await world.service.write(
                user, org_id, company_id, **write_kwargs(content=f"invoicing note {i}")
            )
        results = await world.service.search(user, org_id, company_id, "invoicing", limit=2)
        assert len(results) == 2


# -- Remove / list ----------------------------------------------------------------


class TestRemove:
    async def test_remove_deletes_and_emits_memory_deleted(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        record = await world.service.write(user, org_id, company_id, **write_kwargs())
        await world.service.remove(user, org_id, company_id, record.id)
        assert await world.memory.retrieve(company_id, record.id) is None
        event = world.bus.events[-1]
        assert event.type == "memory.deleted"
        assert event.payload["memory_record_id"] == str(record.id)

    async def test_remove_missing_record_404(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        with pytest.raises(NotFoundError):
            await world.service.remove(user, org_id, company_id, MemoryRecordId.generate())

    async def test_remove_requires_member(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        record = await world.service.write(user, org_id, company_id, **write_kwargs())
        outsider = User.register(email="out4@example.com", hashed_password="x", display_name="Out")
        with pytest.raises(NotAMember):
            await world.service.remove(outsider, org_id, company_id, record.id)


class TestListRecords:
    async def test_list_scope_filter(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        await world.service.write(
            user, org_id, company_id, **write_kwargs(scope="goal", content="one")
        )
        await world.service.write(
            user, org_id, company_id, **write_kwargs(scope="task", content="two")
        )
        await world.service.write(
            user, org_id, company_id, **write_kwargs(scope="goal", content="three")
        )
        goals = await world.service.list_records(user, org_id, company_id, scope="goal")
        assert sorted(r.content for r in goals) == ["one", "three"]
        everything = await world.service.list_records(user, org_id, company_id)
        assert len(everything) == 3

    async def test_list_tenant_isolation(self) -> None:
        world = World()
        user_a, org_a, company_a = await make_member(world)
        await world.service.write(user_a, org_a, company_a, **write_kwargs(content="a"))
        user_b, org_b, company_b = await make_member(world)
        await world.service.write(user_b, org_b, company_b, **write_kwargs(content="b"))
        assert [r.content for r in await world.service.list_records(user_a, org_a, company_a)] == [
            "a"
        ]
        assert [r.content for r in await world.service.list_records(user_b, org_b, company_b)] == [
            "b"
        ]


# -- Port conformance ---------------------------------------------------------------


def test_fake_store_implements_port() -> None:
    store: MemoryPort = FakeMemoryStore()
    assert hasattr(store, "store")
    assert hasattr(store, "retrieve")
    assert hasattr(store, "search")
    assert hasattr(store, "delete")
    assert hasattr(store, "list")
