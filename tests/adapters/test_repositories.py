"""Tests for the SQLAlchemy tenancy repositories (SQLite aiosqlite in-memory)."""

from __future__ import annotations

import dataclasses
import typing

import pytest
from polsia.adapters.persistence.models import Base
from polsia.adapters.persistence.repositories import (
    SqlAlchemyCompanyRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from polsia.core.shared.errors import (
    DuplicateEmail,
    DuplicateMembership,
    DuplicateSlug,
    NotFoundError,
)
from polsia.core.shared.ids import CompanyId, OrganizationId, UserId
from polsia.core.tenancy.entities import Company, Organization, OrgMembership, User
from polsia.core.tenancy.values import CompanyStatus, MembershipRole, UserStatus
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# ----------------------------------------------------------------- fixtures


@pytest.fixture
async def engine() -> typing.AsyncIterator[AsyncEngine]:
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> typing.AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as sess:
        yield sess


# ----------------------------------------------------------------- builders


def make_user(email: str = "alice@example.com", display_name: str = "Alice") -> User:
    return User.register(email=email, hashed_password="hashed-secret", display_name=display_name)


def make_org(owner: User, slug: str = "acme") -> Organization:
    return Organization.create(name="Acme Inc", slug=slug, owner_user_id=owner.id)


def make_membership(org: Organization, user: User, role: MembershipRole) -> OrgMembership:
    return OrgMembership.create(organization_id=org.id, user_id=user.id, role=role)


def make_company(org: Organization, slug: str = "main-co") -> Company:
    return Company.create(
        organization_id=org.id,
        name="Main Co",
        slug=slug,
        identity={"legal_name": "Main Co Ltd"},
        mission="Do things well",
    )


# --------------------------------------------------------------------- users


async def test_user_add_get_roundtrip(session: AsyncSession) -> None:
    repo = SqlAlchemyUserRepository(session)
    user = make_user()
    await repo.add(user)
    await session.commit()

    fetched = await repo.get(user.id)
    assert fetched == user
    assert fetched is not None
    assert fetched.created_at.tzinfo is not None
    assert fetched.updated_at.tzinfo is not None

    assert await repo.get(UserId.generate()) is None


async def test_user_get_by_email(session: AsyncSession) -> None:
    repo = SqlAlchemyUserRepository(session)
    user = make_user(email="bob@example.com")
    await repo.add(user)
    await session.commit()

    assert (await repo.get_by_email("bob@example.com")) == user
    # Email lookup is normalized (strip + lowercase) like User.register does.
    assert (await repo.get_by_email("  BOB@example.com ")) == user
    assert await repo.get_by_email("nobody@example.com") is None


async def test_user_duplicate_email(session: AsyncSession) -> None:
    repo = SqlAlchemyUserRepository(session)
    await repo.add(make_user(email="dup@example.com"))
    await session.commit()

    with pytest.raises(DuplicateEmail):
        await repo.add(make_user(email="dup@example.com", display_name="Other"))

    # The failed insert must not corrupt the session: reads still work.
    assert (await repo.get_by_email("dup@example.com")) is not None


async def test_user_update_roundtrip(session: AsyncSession) -> None:
    repo = SqlAlchemyUserRepository(session)
    user = make_user()
    await repo.add(user)
    await session.commit()

    renamed = User(
        id=user.id,
        email=user.email,
        hashed_password="new-hash",
        display_name="Alicia",
        status=UserStatus.SUSPENDED,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
    await repo.update(renamed)
    await session.commit()

    fetched = await repo.get(user.id)
    assert fetched == renamed
    assert fetched is not None
    assert fetched.status is UserStatus.SUSPENDED
    assert fetched.hashed_password == "new-hash"


async def test_user_update_missing_raises(session: AsyncSession) -> None:
    repo = SqlAlchemyUserRepository(session)
    ghost = make_user(email="ghost@example.com")
    with pytest.raises(NotFoundError):
        await repo.update(ghost)


# -------------------------------------------------------------- organizations


async def test_org_add_get_by_slug_roundtrip(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    owner = make_user()
    await user_repo.add(owner)
    org = make_org(owner)
    await org_repo.add(org)
    await session.commit()

    assert (await org_repo.get(org.id)) == org
    assert (await org_repo.get_by_slug("acme")) == org
    assert await org_repo.get_by_slug("nope") is None
    assert await org_repo.get(OrganizationId.generate()) is None


async def test_org_duplicate_slug(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    owner = make_user()
    await user_repo.add(owner)
    await org_repo.add(make_org(owner, slug="dup-slug"))
    await session.commit()

    with pytest.raises(DuplicateSlug):
        await org_repo.add(make_org(owner, slug="dup-slug"))


async def test_org_list_for_user_includes_owned_and_member(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    member_repo = SqlAlchemyMembershipRepository(session)
    owner = make_user()
    member = make_user(email="member@example.com")
    await user_repo.add(owner)
    await user_repo.add(member)

    owned = make_org(owner, slug="owned-org")
    joined = make_org(owner, slug="joined-org")
    await org_repo.add(owned)
    await org_repo.add(joined)
    await member_repo.add(make_membership(joined, member, MembershipRole.MEMBER))
    await session.commit()

    assert await org_repo.list_for_user(owner.id) == [owned, joined]
    assert await org_repo.list_for_user(member.id) == [joined]


async def test_org_update_roundtrip(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    owner = make_user()
    await user_repo.add(owner)
    org = make_org(owner)
    await org_repo.add(org)
    await session.commit()

    renamed = org.rename("Acme International")
    await org_repo.update(renamed)
    await session.commit()

    assert (await org_repo.get(org.id)) == renamed


# ---------------------------------------------------------------- memberships


async def test_membership_add_get_list_roundtrip(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    member_repo = SqlAlchemyMembershipRepository(session)
    owner = make_user()
    admin = make_user(email="admin@example.com")
    await user_repo.add(owner)
    await user_repo.add(admin)
    org = make_org(owner)
    await org_repo.add(org)
    await org_repo.add(make_org(owner, slug="second-org"))

    m_owner = make_membership(org, owner, MembershipRole.OWNER)
    m_admin = make_membership(org, admin, MembershipRole.ADMIN)
    await member_repo.add(m_owner)
    await member_repo.add(m_admin)
    await session.commit()

    assert (await member_repo.get(org.id, admin.id)) == m_admin
    assert await member_repo.get(org.id, UserId.generate()) is None
    # Tenant-scoped: admin's membership in `org` is not visible from another org.
    other = await org_repo.get_by_slug("second-org")
    assert other is not None
    assert await member_repo.get(other.id, admin.id) is None

    listed = await member_repo.list_for_organization(org.id)
    assert listed == [m_owner, m_admin]
    assert await member_repo.list_for_organization(other.id) == []


async def test_membership_duplicate(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    member_repo = SqlAlchemyMembershipRepository(session)
    owner = make_user()
    member = make_user(email="member@example.com")
    await user_repo.add(owner)
    await user_repo.add(member)
    org = make_org(owner)
    await org_repo.add(org)
    await member_repo.add(make_membership(org, member, MembershipRole.MEMBER))
    await session.commit()

    with pytest.raises(DuplicateMembership):
        await member_repo.add(make_membership(org, member, MembershipRole.ADMIN))


async def test_membership_update_role_roundtrip(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    member_repo = SqlAlchemyMembershipRepository(session)
    owner = make_user()
    member = make_user(email="member@example.com")
    await user_repo.add(owner)
    await user_repo.add(member)
    org = make_org(owner)
    await org_repo.add(org)
    membership = make_membership(org, member, MembershipRole.MEMBER)
    await member_repo.add(membership)
    await session.commit()

    promoted = dataclasses.replace(membership, role=MembershipRole.ADMIN)
    await member_repo.update(promoted)
    await session.commit()

    fetched = await member_repo.get(org.id, member.id)
    assert fetched == promoted
    assert fetched is not None
    assert fetched.role is MembershipRole.ADMIN


# ------------------------------------------------------------------- companies


async def test_company_add_get_roundtrip_with_json_fields(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    company_repo = SqlAlchemyCompanyRepository(session)
    owner = make_user()
    await user_repo.add(owner)
    org = make_org(owner)
    await org_repo.add(org)
    company = make_company(org)
    company = company.update_strategy({"north_star": "retained revenue"})
    company = company.update_configuration({"cadence": "weekly"})
    await company_repo.add(company)
    await session.commit()

    fetched = await company_repo.get(org.id, company.id)
    assert fetched == company
    assert fetched is not None
    assert fetched.identity == {"legal_name": "Main Co Ltd"}
    assert fetched.strategy == {"north_star": "retained revenue"}
    assert fetched.configuration == {"cadence": "weekly"}
    assert fetched.mission == "Do things well"
    assert fetched.status is CompanyStatus.DRAFT

    assert await company_repo.get(CompanyId.generate(), company.id) is None


async def test_company_get_is_tenant_scoped(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    company_repo = SqlAlchemyCompanyRepository(session)
    owner = make_user()
    await user_repo.add(owner)
    org_a = make_org(owner, slug="org-a")
    org_b = make_org(owner, slug="org-b")
    await org_repo.add(org_a)
    await org_repo.add(org_b)
    company = make_company(org_a)
    await company_repo.add(company)
    await session.commit()

    # Same company id, wrong organization id -> not found (no cross-tenant leak).
    assert await company_repo.get(org_b.id, company.id) is None
    assert await company_repo.get(org_a.id, company.id) == company


async def test_company_get_by_slug_and_list(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    company_repo = SqlAlchemyCompanyRepository(session)
    owner = make_user()
    await user_repo.add(owner)
    org_a = make_org(owner, slug="org-a")
    org_b = make_org(owner, slug="org-b")
    await org_repo.add(org_a)
    await org_repo.add(org_b)
    c1 = make_company(org_a, slug="alpha")
    c2 = make_company(org_a, slug="beta")
    c3 = make_company(org_b, slug="gamma")
    await company_repo.add(c1)
    await company_repo.add(c2)
    await company_repo.add(c3)
    await session.commit()

    assert (await company_repo.get_by_slug(org_a.id, "beta")) == c2
    assert await company_repo.get_by_slug(org_b.id, "beta") is None  # scoped lookup
    assert await company_repo.get_by_slug(org_a.id, "missing") is None
    assert await company_repo.list_for_organization(org_a.id) == [c1, c2]
    assert await company_repo.list_for_organization(org_b.id) == [c3]


async def test_company_duplicate_slug_is_per_organization(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    company_repo = SqlAlchemyCompanyRepository(session)
    owner = make_user()
    await user_repo.add(owner)
    org_a = make_org(owner, slug="org-a")
    org_b = make_org(owner, slug="org-b")
    await org_repo.add(org_a)
    await org_repo.add(org_b)
    await company_repo.add(make_company(org_a, slug="same-slug"))
    await session.commit()

    with pytest.raises(DuplicateSlug):
        await company_repo.add(make_company(org_a, slug="same-slug"))

    # Same slug in a DIFFERENT organization is allowed (unique per tenant).
    other = make_company(org_b, slug="same-slug")
    await company_repo.add(other)
    await session.commit()
    assert (await company_repo.get(org_b.id, other.id)) == other


async def test_company_update_json_fields_roundtrip(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    company_repo = SqlAlchemyCompanyRepository(session)
    owner = make_user()
    await user_repo.add(owner)
    org = make_org(owner)
    await org_repo.add(org)
    company = make_company(org)
    await company_repo.add(company)
    await session.commit()

    changed = company.rename("Renamed Co")
    changed = changed.update_identity({"legal_name": "New Legal", "country": "DE"})
    changed = changed.update_mission(None)
    changed = changed.update_strategy({"bets": ["a", "b"], "nested": {"k": [1, 2]}})
    changed = changed.update_configuration({"feature_flags": {"x": True}})
    changed = changed.transition_status(CompanyStatus.ACTIVE)
    await company_repo.update(changed)
    await session.commit()

    fetched = await company_repo.get(org.id, company.id)
    assert fetched == changed
    assert fetched is not None
    assert fetched.identity == {"legal_name": "New Legal", "country": "DE"}
    assert fetched.strategy == {"bets": ["a", "b"], "nested": {"k": [1, 2]}}
    assert fetched.configuration == {"feature_flags": {"x": True}}
    assert fetched.mission is None
    assert fetched.status is CompanyStatus.ACTIVE


async def test_company_update_missing_raises(session: AsyncSession) -> None:
    user_repo = SqlAlchemyUserRepository(session)
    org_repo = SqlAlchemyOrganizationRepository(session)
    company_repo = SqlAlchemyCompanyRepository(session)
    owner = make_user()
    await user_repo.add(owner)
    org = make_org(owner)
    await org_repo.add(org)
    ghost = make_company(org)
    with pytest.raises(NotFoundError):
        await company_repo.update(ghost)
