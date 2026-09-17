"""OrganizationService / CompanyService tests with in-memory fakes.

Covers authorization ranks (ADR-0002), owner-membership auto-creation,
event emission (types + payloads, ADR-0004) and status transitions.
"""

from __future__ import annotations

import pytest
from polsia.core.shared.errors import (
    CompanyNotFoundError,
    DuplicateMembership,
    DuplicateSlug,
    InsufficientRole,
    InvalidStatusTransition,
    NotAMember,
    OrganizationNotFoundError,
)
from polsia.core.shared.events import EventEnvelope
from polsia.core.shared.ids import CompanyId, OrganizationId, UserId
from polsia.core.tenancy.entities import Company, Organization, OrgMembership, User
from polsia.core.tenancy.values import CompanyStatus, MembershipRole
from polsia.services.tenancy import CompanyService, OrganizationService

# -- In-memory fakes -----------------------------------------------------------


class FakeMembershipRepository:
    def __init__(self) -> None:
        self.by_id: dict[str, OrgMembership] = {}
        self.pairs: dict[tuple[OrganizationId, UserId], OrgMembership] = {}

    async def add(self, membership: OrgMembership) -> None:
        key = (membership.organization_id, membership.user_id)
        if key in self.pairs:
            raise DuplicateMembership("duplicate membership")
        self.by_id[str(membership.id)] = membership
        self.pairs[key] = membership

    async def get(self, organization_id: OrganizationId, user_id: UserId) -> OrgMembership | None:
        return self.pairs.get((organization_id, user_id))

    async def list_for_organization(self, organization_id: OrganizationId) -> list[OrgMembership]:
        return [m for m in self.by_id.values() if m.organization_id == organization_id]

    async def update(self, membership: OrgMembership) -> None:
        self.by_id[str(membership.id)] = membership


class FakeOrganizationRepository:
    def __init__(self, memberships: FakeMembershipRepository) -> None:
        self._memberships = memberships
        self.by_id: dict[OrganizationId, Organization] = {}
        self.by_slug: dict[str, Organization] = {}

    async def add(self, organization: Organization) -> None:
        if organization.slug in self.by_slug:
            raise DuplicateSlug("duplicate org slug")
        self.by_id[organization.id] = organization
        self.by_slug[organization.slug] = organization

    async def get(self, organization_id: OrganizationId) -> Organization | None:
        return self.by_id.get(organization_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        return self.by_slug.get(slug)

    async def list_for_user(self, user_id: UserId) -> list[Organization]:
        member_orgs = {org_id for (org_id, uid) in self._memberships.pairs if uid == user_id}
        return [o for o in self.by_id.values() if o.owner_user_id == user_id or o.id in member_orgs]

    async def update(self, organization: Organization) -> None:
        self.by_id[organization.id] = organization
        self.by_slug[organization.slug] = organization


class FakeCompanyRepository:
    def __init__(self) -> None:
        self.by_id: dict[CompanyId, Company] = {}

    async def add(self, company: Company) -> None:
        for existing in self.by_id.values():
            if (
                existing.organization_id == company.organization_id
                and existing.slug == company.slug
            ):
                raise DuplicateSlug("duplicate company slug")
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


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)

    def subscribe(self, pattern: str, handler: object) -> None:  # pragma: no cover
        pass

    def of_type(self, event_type: str) -> list[EventEnvelope]:
        return [e for e in self.events if e.type == event_type]


# -- Fixtures / helpers ------------------------------------------------------------


def make_user(name: str) -> User:
    return User.register(email=f"{name}@example.com", hashed_password="hashed", display_name=name)


@pytest.fixture
def memberships() -> FakeMembershipRepository:
    return FakeMembershipRepository()


@pytest.fixture
def organizations(memberships: FakeMembershipRepository) -> FakeOrganizationRepository:
    return FakeOrganizationRepository(memberships)


@pytest.fixture
def companies() -> FakeCompanyRepository:
    return FakeCompanyRepository()


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


@pytest.fixture
def org_service(
    organizations: FakeOrganizationRepository,
    memberships: FakeMembershipRepository,
    bus: RecordingBus,
) -> OrganizationService:
    return OrganizationService(organizations, memberships, bus)


@pytest.fixture
def company_service(
    companies: FakeCompanyRepository,
    memberships: FakeMembershipRepository,
    bus: RecordingBus,
) -> CompanyService:
    return CompanyService(companies, memberships, bus)


@pytest.fixture
async def org(
    org_service: OrganizationService, memberships: FakeMembershipRepository
) -> Organization:
    owner = make_user("owner")
    return await org_service.create_organization(owner, "Acme Corp", "acme")


def _owner_of(org: Organization) -> User:
    return User(
        id=org.owner_user_id,
        email="owner@example.com",
        hashed_password="hashed",
        display_name="Owner",
    )


class TestCreateOrganization:
    async def test_owner_membership_auto_created(
        self,
        org_service: OrganizationService,
        memberships: FakeMembershipRepository,
        bus: RecordingBus,
    ) -> None:
        owner = make_user("alice")
        org = await org_service.create_organization(owner, "Acme", "acme")

        membership = await memberships.get(org.id, owner.id)
        assert membership is not None
        assert membership.role is MembershipRole.OWNER

    async def test_emits_organization_created(
        self, org_service: OrganizationService, bus: RecordingBus
    ) -> None:
        owner = make_user("bob")
        org = await org_service.create_organization(owner, "Acme", "acme")

        assert len(bus.events) == 1  # owner membership is implicit, not announced
        event = bus.events[0]
        assert event.type == "organization.created"
        assert event.organization_id == org.id
        assert event.payload["name"] == "Acme"
        assert event.payload["slug"] == "acme"
        assert event.payload["owner_user_id"] == str(owner.id)

    async def test_duplicate_slug_rejected(self, org_service: OrganizationService) -> None:
        owner = make_user("carol")
        await org_service.create_organization(owner, "Acme", "acme")
        with pytest.raises(DuplicateSlug):
            await org_service.create_organization(owner, "Acme Two", "acme")


class TestGetOrganization:
    async def test_owner_can_get(self, org_service: OrganizationService, org: Organization) -> None:
        fetched = await org_service.get_organization_for_user(org.id, _owner_of(org))
        assert fetched.id == org.id

    async def test_missing_organization_raises_not_found(
        self, org_service: OrganizationService
    ) -> None:
        user = make_user("dave")
        with pytest.raises(OrganizationNotFoundError):
            await org_service.get_organization_for_user(OrganizationId.generate(), user)

    async def test_non_member_raises_not_a_member(
        self, org_service: OrganizationService, org: Organization
    ) -> None:
        outsider = make_user("eve")
        with pytest.raises(NotAMember):
            await org_service.get_organization_for_user(org.id, outsider)


class TestListOrganizations:
    async def test_lists_owned_and_member_orgs(
        self,
        org_service: OrganizationService,
        memberships: FakeMembershipRepository,
    ) -> None:
        owner = make_user("frank")
        owned = await org_service.create_organization(owner, "Owned", "owned")
        other_owner = make_user("gina")
        joined = await org_service.create_organization(other_owner, "Joined", "joined")
        await org_service.add_member(other_owner, joined.id, owner.id, MembershipRole.MEMBER)
        stranger = make_user("hank")
        await org_service.create_organization(stranger, "Hidden", "hidden")

        result = await org_service.list_organizations_for_user(owner)
        ids = {o.id for o in result}
        assert ids == {owned.id, joined.id}


class TestAddMember:
    async def test_owner_adds_member(
        self,
        org_service: OrganizationService,
        memberships: FakeMembershipRepository,
        bus: RecordingBus,
        org: Organization,
    ) -> None:
        owner = _owner_of(org)
        new_user = make_user("newbie")

        membership = await org_service.add_member(owner, org.id, new_user.id, MembershipRole.MEMBER)

        assert membership.role is MembershipRole.MEMBER
        stored = await memberships.get(org.id, new_user.id)
        assert stored is not None and stored.id == membership.id
        event = bus.of_type("membership.created")[0]
        assert event.payload["user_id"] == str(new_user.id)
        assert event.payload["role"] == "member"

    async def test_admin_can_add_member(
        self,
        org_service: OrganizationService,
        organizations: FakeOrganizationRepository,
        memberships: FakeMembershipRepository,
        bus: RecordingBus,
        org: Organization,
    ) -> None:
        owner = _owner_of(org)
        admin = make_user("the-admin")
        await org_service.add_member(owner, org.id, admin.id, MembershipRole.ADMIN)
        bus.events.clear()

        new_user = make_user("brand-new")
        await org_service.add_member(admin, org.id, new_user.id, MembershipRole.MEMBER)
        assert await memberships.get(org.id, new_user.id) is not None
        assert len(bus.of_type("membership.created")) == 1

    async def test_member_cannot_add_member(
        self, org_service: OrganizationService, org: Organization
    ) -> None:
        owner = _owner_of(org)
        member = make_user("plain-member")
        await org_service.add_member(owner, org.id, member.id, MembershipRole.MEMBER)
        other = make_user("target")

        with pytest.raises(InsufficientRole):
            await org_service.add_member(member, org.id, other.id, MembershipRole.MEMBER)

    async def test_non_member_cannot_add_member(
        self, org_service: OrganizationService, org: Organization
    ) -> None:
        outsider = make_user("outsider")
        target = make_user("target")
        with pytest.raises(NotAMember):
            await org_service.add_member(outsider, org.id, target.id, MembershipRole.MEMBER)

    async def test_duplicate_membership_rejected(
        self, org_service: OrganizationService, org: Organization
    ) -> None:
        owner = _owner_of(org)
        member = make_user("dupe")
        await org_service.add_member(owner, org.id, member.id, MembershipRole.MEMBER)
        with pytest.raises(DuplicateMembership):
            await org_service.add_member(owner, org.id, member.id, MembershipRole.MEMBER)


class TestListMembers:
    async def test_member_can_list(
        self, org_service: OrganizationService, org: Organization
    ) -> None:
        owner = _owner_of(org)
        member = make_user("lister")
        await org_service.add_member(owner, org.id, member.id, MembershipRole.MEMBER)

        members = await org_service.list_members(member, org.id)
        assert {m.user_id for m in members} == {owner.id, member.id}

    async def test_non_member_cannot_list(
        self, org_service: OrganizationService, org: Organization
    ) -> None:
        outsider = make_user("snoop")
        with pytest.raises(NotAMember):
            await org_service.list_members(outsider, org.id)


class TestRename:
    async def test_owner_renames(
        self, org_service: OrganizationService, bus: RecordingBus, org: Organization
    ) -> None:
        renamed = await org_service.rename(_owner_of(org), org.id, "New Name")
        assert renamed.name == "New Name"
        event = bus.of_type("organization.renamed")[0]
        assert event.payload["name"] == "New Name"

    async def test_member_cannot_rename(
        self, org_service: OrganizationService, org: Organization
    ) -> None:
        owner = _owner_of(org)
        member = make_user("m")
        await org_service.add_member(owner, org.id, member.id, MembershipRole.MEMBER)
        with pytest.raises(InsufficientRole):
            await org_service.rename(member, org.id, "Sneaky Name")


# -- CompanyService ------------------------------------------------------------


class TestCreateCompany:
    async def test_member_can_create_company(
        self,
        org_service: OrganizationService,
        company_service: CompanyService,
        companies: FakeCompanyRepository,
        bus: RecordingBus,
        org: Organization,
    ) -> None:
        owner = _owner_of(org)

        company = await company_service.create_company(
            owner, org.id, "Coffee Shop", "coffee-shop", mission="Serve coffee"
        )

        assert company.status is CompanyStatus.DRAFT
        assert company.organization_id == org.id
        assert company.mission == "Serve coffee"
        assert (await companies.get(org.id, company.id)) is not None
        event = bus.of_type("company.created")[0]
        assert event.type == "company.created"
        assert event.organization_id == org.id
        assert event.company_id == company.id
        assert event.payload == {"name": "Coffee Shop", "slug": "coffee-shop"}

    async def test_non_member_cannot_create_company(
        self, company_service: CompanyService, org: Organization
    ) -> None:
        outsider = make_user("nope")
        with pytest.raises(NotAMember):
            await company_service.create_company(outsider, org.id, "Sneaky Co", "sneaky-co")

    async def test_duplicate_company_slug_rejected(
        self,
        org_service: OrganizationService,
        company_service: CompanyService,
        org: Organization,
    ) -> None:
        owner = _owner_of(org)
        await company_service.create_company(owner, org.id, "One", "same-slug")
        with pytest.raises(DuplicateSlug):
            await company_service.create_company(owner, org.id, "Two", "same-slug")


class TestGetCompany:
    async def test_member_gets_company(
        self,
        org_service: OrganizationService,
        company_service: CompanyService,
        org: Organization,
    ) -> None:
        owner = _owner_of(org)
        member = make_user("viewer")
        await org_service.add_member(owner, org.id, member.id, MembershipRole.MEMBER)
        created = await company_service.create_company(owner, org.id, "Bakery", "bakery")

        fetched = await company_service.get_company(member, org.id, created.id)
        assert fetched.id == created.id

    async def test_non_member_gets_not_a_member(
        self, company_service: CompanyService, org: Organization
    ) -> None:
        outsider = make_user("peeker")
        owner = _owner_of(org)
        company = await company_service.create_company(owner, org.id, "Biz", "biz")
        with pytest.raises(NotAMember):
            await company_service.get_company(outsider, org.id, company.id)

    async def test_company_under_wrong_org_is_not_found(
        self,
        org_service: OrganizationService,
        company_service: CompanyService,
        companies: FakeCompanyRepository,
        org: Organization,
    ) -> None:
        owner = _owner_of(org)
        other_owner = make_user("other-org-owner")
        other_org = await org_service.create_organization(other_owner, "Other", "other")
        company = await company_service.create_company(other_owner, other_org.id, "X", "x")
        # Owner of `org` asks for the other org's company under `org` -> 404.
        with pytest.raises(CompanyNotFoundError):
            await company_service.get_company(owner, org.id, company.id)

        # Sanity: the company exists under its real organization.
        assert await companies.get(other_org.id, company.id) is not None


class TestListCompanies:
    async def test_lists_only_scoped_companies(
        self,
        org_service: OrganizationService,
        company_service: CompanyService,
        org: Organization,
    ) -> None:
        owner = _owner_of(org)
        await company_service.create_company(owner, org.id, "A", "a")
        await company_service.create_company(owner, org.id, "B", "b")

        result = await company_service.list_companies(owner, org.id)
        assert {c.slug for c in result} == {"a", "b"}


class TestUpdateCompany:
    async def test_updates_fields_and_emits_event(
        self,
        company_service: CompanyService,
        bus: RecordingBus,
        org: Organization,
    ) -> None:
        owner = _owner_of(org)
        company = await company_service.create_company(owner, org.id, "Cafe", "cafe")
        bus.events.clear()

        updated = await company_service.update_company(
            owner,
            org.id,
            company.id,
            name="Cafe Prime",
            identity={"cuisine": "coffee"},
            mission="Best coffee in town",
            strategy={"markets": ["EU"]},
            configuration={"opening_hours": "08:00-18:00"},
        )

        assert updated.name == "Cafe Prime"
        assert updated.identity == {"cuisine": "coffee"}
        assert updated.mission == "Best coffee in town"
        assert updated.strategy == {"markets": ["EU"]}
        assert updated.configuration == {"opening_hours": "08:00-18:00"}
        event = bus.of_type("company.updated")[0]
        assert event.company_id == company.id
        # No business semantics in event payloads — name/id/slug only.
        assert event.payload == {"name": "Cafe Prime"}

    async def test_partial_update_keeps_other_fields(
        self, company_service: CompanyService, org: Organization
    ) -> None:
        owner = _owner_of(org)
        company = await company_service.create_company(
            owner, org.id, "Cafe", "cafe", mission="Original mission"
        )
        updated = await company_service.update_company(
            owner, org.id, company.id, mission="New mission"
        )
        assert updated.mission == "New mission"
        assert updated.name == "Cafe"

    async def test_non_member_cannot_update(
        self, company_service: CompanyService, org: Organization
    ) -> None:
        owner = _owner_of(org)
        company = await company_service.create_company(owner, org.id, "Cafe", "cafe")
        outsider = make_user("vandal")
        with pytest.raises(NotAMember):
            await company_service.update_company(outsider, org.id, company.id, name="Hacked")


class TestChangeStatus:
    async def test_valid_transition_emits_event(
        self,
        company_service: CompanyService,
        bus: RecordingBus,
        org: Organization,
    ) -> None:
        owner = _owner_of(org)
        company = await company_service.create_company(owner, org.id, "Cafe", "cafe")
        bus.events.clear()

        updated = await company_service.change_status(
            owner, org.id, company.id, CompanyStatus.ACTIVE
        )

        assert updated.status is CompanyStatus.ACTIVE
        event = bus.of_type("company.status_changed")[0]
        assert event.payload == {"from": "draft", "to": "active"}

    async def test_invalid_transition_raises_and_emits_nothing(
        self,
        company_service: CompanyService,
        bus: RecordingBus,
        org: Organization,
    ) -> None:
        owner = _owner_of(org)
        company = await company_service.create_company(owner, org.id, "Cafe", "cafe")
        with pytest.raises(InvalidStatusTransition):
            await company_service.change_status(owner, org.id, company.id, CompanyStatus.PAUSED)
        assert bus.of_type("company.status_changed") == []
