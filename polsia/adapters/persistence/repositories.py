"""SQLAlchemy implementations of the tenancy repository ports.

Contract with the caller:
- Repositories do NOT commit; they flush so that adapter failures surface at
  the repository boundary and can be translated into domain errors. Commit /
  rollback of the unit of work belongs to the session provider (polsia.infra.db).
- On an IntegrityError the repository rolls the session back (resetting its
  failed state) and raises the translated domain error.
- On `update`, a missing row raises NotFoundError (domain error taxonomy).

Entities are frozen dataclasses, so updates copy column values from the
entity onto the tracked ORM row.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.models import (
    CompanyModel,
    OrganizationModel,
    OrgMembershipModel,
    UserModel,
)
from polsia.adapters.persistence.translate import translate_integrity_error
from polsia.core.shared.errors import NotFoundError
from polsia.core.shared.ids import CompanyId, MembershipId, OrganizationId, UserId
from polsia.core.tenancy.entities import Company, Organization, OrgMembership, User
from polsia.core.tenancy.values import CompanyStatus, MembershipRole, OrganizationStatus, UserStatus


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite stores timestamps without tzinfo; re-anchor naive values to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        row = UserModel(
            id=str(user.id),
            email=user.email,
            hashed_password=user.hashed_password,
            display_name=user.display_name,
            status=user.status.value,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:  # pragma: no branch - exact branch shown by tests
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, user_id: UserId) -> User | None:
        row = await self._session.get(UserModel, str(user_id))
        return _to_user(row) if row is not None else None

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(UserModel).where(UserModel.email == email.strip().lower())
        row = (await self._session.scalars(stmt)).first()
        return _to_user(row) if row is not None else None

    async def update(self, user: User) -> None:
        row = await self._session.get(UserModel, str(user.id))
        if row is None:
            raise NotFoundError(f"user {user.id} not found")
        row.email = user.email
        row.hashed_password = user.hashed_password
        row.display_name = user.display_name
        row.status = user.status.value
        row.updated_at = user.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc


def _to_user(row: UserModel) -> User:
    return User(
        id=UserId.parse(row.id),
        email=row.email,
        hashed_password=row.hashed_password,
        display_name=row.display_name,
        status=UserStatus(row.status),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


class SqlAlchemyOrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, organization: Organization) -> None:
        row = OrganizationModel(
            id=str(organization.id),
            name=organization.name,
            slug=organization.slug,
            owner_user_id=str(organization.owner_user_id),
            status=organization.status.value,
            created_at=organization.created_at,
            updated_at=organization.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, organization_id: OrganizationId) -> Organization | None:
        row = await self._session.get(OrganizationModel, str(organization_id))
        return _to_organization(row) if row is not None else None

    async def get_by_slug(self, slug: str) -> Organization | None:
        stmt = select(OrganizationModel).where(OrganizationModel.slug == slug)
        row = (await self._session.scalars(stmt)).first()
        return _to_organization(row) if row is not None else None

    async def list_for_user(self, user_id: UserId) -> list[Organization]:
        """Organizations the user owns or belongs to via a membership."""
        member_of = exists().where(
            OrgMembershipModel.organization_id == OrganizationModel.id,
            OrgMembershipModel.user_id == str(user_id),
        )
        stmt = (
            select(OrganizationModel)
            .where(
                (OrganizationModel.owner_user_id == str(user_id)) | member_of,
            )
            .order_by(OrganizationModel.created_at, OrganizationModel.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_organization(row) for row in rows]

    async def update(self, organization: Organization) -> None:
        row = await self._session.get(OrganizationModel, str(organization.id))
        if row is None:
            raise NotFoundError(f"organization {organization.id} not found")
        row.name = organization.name
        row.slug = organization.slug
        row.owner_user_id = str(organization.owner_user_id)
        row.status = organization.status.value
        row.updated_at = organization.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc


def _to_organization(row: OrganizationModel) -> Organization:
    return Organization(
        id=OrganizationId.parse(row.id),
        name=row.name,
        slug=row.slug,
        owner_user_id=UserId.parse(row.owner_user_id),
        status=OrganizationStatus(row.status),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


class SqlAlchemyMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, membership: OrgMembership) -> None:
        row = OrgMembershipModel(
            id=str(membership.id),
            organization_id=str(membership.organization_id),
            user_id=str(membership.user_id),
            role=membership.role.value,
            created_at=membership.created_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, organization_id: OrganizationId, user_id: UserId) -> OrgMembership | None:
        stmt = select(OrgMembershipModel).where(
            OrgMembershipModel.organization_id == str(organization_id),
            OrgMembershipModel.user_id == str(user_id),
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_membership(row) if row is not None else None

    async def list_for_organization(self, organization_id: OrganizationId) -> list[OrgMembership]:
        stmt = (
            select(OrgMembershipModel)
            .where(OrgMembershipModel.organization_id == str(organization_id))
            .order_by(OrgMembershipModel.created_at, OrgMembershipModel.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_membership(row) for row in rows]

    async def update(self, membership: OrgMembership) -> None:
        row = await self._session.get(OrgMembershipModel, str(membership.id))
        if row is None:
            raise NotFoundError(f"membership {membership.id} not found")
        row.role = membership.role.value
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc


def _to_membership(row: OrgMembershipModel) -> OrgMembership:
    return OrgMembership(
        id=MembershipId.parse(row.id),
        organization_id=OrganizationId.parse(row.organization_id),
        user_id=UserId.parse(row.user_id),
        role=MembershipRole(row.role),
        created_at=_aware(row.created_at),
    )


class SqlAlchemyCompanyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, company: Company) -> None:
        row = CompanyModel(
            id=str(company.id),
            organization_id=str(company.organization_id),
            name=company.name,
            slug=company.slug,
            identity=dict(company.identity),
            mission=company.mission,
            strategy=dict(company.strategy),
            configuration=dict(company.configuration),
            status=company.status.value,
            created_at=company.created_at,
            updated_at=company.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, organization_id: OrganizationId, company_id: CompanyId) -> Company | None:
        """Tenant-scoped fetch: a company id under the wrong organization is not found."""
        stmt = select(CompanyModel).where(
            CompanyModel.id == str(company_id),
            CompanyModel.organization_id == str(organization_id),
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_company(row) if row is not None else None

    async def get_by_slug(self, organization_id: OrganizationId, slug: str) -> Company | None:
        stmt = select(CompanyModel).where(
            CompanyModel.organization_id == str(organization_id),
            CompanyModel.slug == slug,
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_company(row) if row is not None else None

    async def list_for_organization(self, organization_id: OrganizationId) -> list[Company]:
        stmt = (
            select(CompanyModel)
            .where(CompanyModel.organization_id == str(organization_id))
            .order_by(CompanyModel.created_at, CompanyModel.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_company(row) for row in rows]

    async def update(self, company: Company) -> None:
        row = await self._session.get(CompanyModel, str(company.id))
        if row is None:
            raise NotFoundError(f"company {company.id} not found")
        row.name = company.name
        row.slug = company.slug
        row.identity = dict(company.identity)
        row.mission = company.mission
        row.strategy = dict(company.strategy)
        row.configuration = dict(company.configuration)
        row.status = company.status.value
        row.updated_at = company.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc


def _to_company(row: CompanyModel) -> Company:
    identity: dict[str, Any] = dict(row.identity or {})
    strategy: dict[str, Any] = dict(row.strategy or {})
    configuration: dict[str, Any] = dict(row.configuration or {})
    return Company(
        id=CompanyId.parse(row.id),
        organization_id=OrganizationId.parse(row.organization_id),
        name=row.name,
        slug=row.slug,
        identity=identity,
        mission=row.mission,
        strategy=strategy,
        configuration=configuration,
        status=CompanyStatus(row.status),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )
