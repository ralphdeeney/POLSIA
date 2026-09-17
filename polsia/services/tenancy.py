"""Tenancy use cases: OrganizationService and CompanyService.

Application-layer services between the API routers and the core ports:

- Constructor-injected ports (repositories, event bus, optional clock) —
  no framework or adapter imports here beyond the core contracts.
- Authorization (ADR-0002): every organization/company operation takes an
  explicit organization scope and verifies the actor's membership via
  ``MembershipRepository.get(org_id, user_id)``. Absent membership raises
  :class:`NotAMember`; an insufficient role raises :class:`InsufficientRole`.
  Owner privilege is implicit: the organization's ``owner_user_id`` ranks as
  OWNER even before/without a membership row lookup succeeding.
- Events (ADR-0004): after a successful repository mutation the service
  publishes an :class:`EventEnvelope`. Payloads carry only ids (as str),
  names and slugs — never business semantics.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from polsia.core.shared.errors import (
    CompanyNotFoundError,
    DuplicateMembership,
    DuplicateSlug,
    InsufficientRole,
    NotAMember,
    OrganizationNotFoundError,
    UserNotFoundError,
)
from polsia.core.shared.events import EventBus, EventEnvelope
from polsia.core.shared.ids import CompanyId, OrganizationId, UserId
from polsia.core.tenancy.entities import Company, Organization, OrgMembership, User
from polsia.core.tenancy.ports import (
    CompanyRepository,
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)
from polsia.core.tenancy.values import CompanyStatus, MembershipRole

#: Optional injectable clock (returns timezone-aware UTC by contract).
Clock = Callable[[], dt.datetime]


def _now(clock: Clock | None) -> dt.datetime | None:
    return clock() if clock is not None else None


def _publish(
    bus: EventBus,
    event_type: str,
    *,
    organization_id: OrganizationId,
    company_id: CompanyId | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    bus.publish(
        EventEnvelope(
            event_type=event_type,
            organization_id=organization_id,
            company_id=company_id,
            payload=payload,
        )
    )


class OrganizationService:
    """Use cases for organizations and their memberships."""

    def __init__(
        self,
        organizations: OrganizationRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        clock: Clock | None = None,
        users: UserRepository | None = None,
    ) -> None:
        self._organizations = organizations
        self._memberships = memberships
        self._bus = bus
        self._clock = clock
        # Optional: when wired, add_member verifies the target user exists
        # (404) instead of relying on the FK constraint (which surfaces as a
        # misleading 409 from the adapter).
        self._users = users

    # -- queries ----------------------------------------------------------

    async def list_organizations_for_user(self, user: User) -> list[Organization]:
        return await self._organizations.list_for_user(user.id)

    async def get_organization_for_user(
        self, organization_id: OrganizationId, user: User
    ) -> Organization:
        """Return the organization; 404 if missing, NotAMember if the user
        is neither a member nor the owner."""
        return await self._authorize(user, organization_id, MembershipRole.MEMBER)

    async def list_members(
        self, actor: User, organization_id: OrganizationId
    ) -> list[OrgMembership]:
        """List members of the organization (requires the member role)."""
        await self._authorize(actor, organization_id, MembershipRole.MEMBER)
        return await self._memberships.list_for_organization(organization_id)

    # -- commands ----------------------------------------------------------

    async def create_organization(self, owner: User, name: str, slug: str) -> Organization:
        """Create an organization owned by ``owner`` plus the OWNER membership.

        Emits ``organization.created`` (the owner membership is implicit and
        deliberately not announced as a separate event).
        """
        existing = await self._organizations.get_by_slug(slug)
        if existing is not None:
            raise DuplicateSlug(f"an organization with slug {slug!r} already exists")
        organization = Organization.create(
            name=name, slug=slug, owner_user_id=owner.id, now=_now(self._clock)
        )
        await self._organizations.add(organization)
        await self._memberships.add(
            OrgMembership.create(
                organization_id=organization.id,
                user_id=owner.id,
                role=MembershipRole.OWNER,
            )
        )
        _publish(
            self._bus,
            "organization.created",
            organization_id=organization.id,
            payload={
                "organization_id": str(organization.id),
                "name": organization.name,
                "slug": organization.slug,
                "owner_user_id": str(owner.id),
            },
        )
        return organization

    async def add_member(
        self,
        actor: User,
        organization_id: OrganizationId,
        new_user_id: UserId,
        role: MembershipRole,
    ) -> OrgMembership:
        """Add ``new_user_id`` to the organization (requires the admin role).

        Raises :class:`DuplicateMembership` when the user is already a member
        and :class:`UserNotFoundError` when the target user does not exist.
        """
        await self._authorize(actor, organization_id, MembershipRole.ADMIN)
        if self._users is not None:
            target = await self._users.get(new_user_id)
            if target is None:
                raise UserNotFoundError(f"user {new_user_id} not found")
        if await self._memberships.get(organization_id, new_user_id) is not None:
            raise DuplicateMembership(
                f"user {new_user_id} is already a member of organization {organization_id}"
            )
        membership = OrgMembership.create(
            organization_id=organization_id, user_id=new_user_id, role=role
        )
        await self._memberships.add(membership)
        _publish(
            self._bus,
            "membership.created",
            organization_id=organization_id,
            payload={
                "membership_id": str(membership.id),
                "user_id": str(new_user_id),
                "role": role.value,
            },
        )
        return membership

    async def rename(self, actor: User, organization_id: OrganizationId, name: str) -> Organization:
        """Rename the organization (requires the admin role)."""
        organization = await self._authorize(actor, organization_id, MembershipRole.ADMIN)
        renamed = organization.rename(name)
        await self._organizations.update(renamed)
        _publish(
            self._bus,
            "organization.renamed",
            organization_id=organization_id,
            payload={"name": renamed.name},
        )
        return renamed

    # -- internal ----------------------------------------------------------

    async def _load_organization(self, organization_id: OrganizationId) -> Organization:
        organization = await self._organizations.get(organization_id)
        if organization is None:
            raise OrganizationNotFoundError(f"organization {organization_id} not found")
        return organization

    async def _authorize(
        self, actor: User, organization_id: OrganizationId, required: MembershipRole
    ) -> Organization:
        """Load the org and verify the actor holds at least ``required``.

        Membership is resolved from MembershipRepository; a missing row falls
        back to owner privilege when the actor owns the organization.
        """
        organization = await self._load_organization(organization_id)
        membership = await self._memberships.get(organization_id, actor.id)
        if membership is not None:
            role = membership.role
        elif organization.owner_user_id == actor.id:
            role = MembershipRole.OWNER
        else:
            raise NotAMember(f"user {actor.id} is not a member of organization {organization_id}")
        if not role.at_least(required):
            raise InsufficientRole(
                f"this action requires the {required.value} role (actor holds {role.value})"
            )
        return organization


class CompanyService:
    """Use cases for companies, always scoped by an explicit organization."""

    def __init__(
        self,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        clock: Clock | None = None,
    ) -> None:
        self._companies = companies
        self._memberships = memberships
        self._bus = bus
        self._clock = clock

    # -- queries ----------------------------------------------------------

    async def get_company(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId
    ) -> Company:
        """Fetch a company within the organization scope (member required).

        A company id under a different organization is simply not found —
        the organization id is part of every lookup (ADR-0002).
        """
        await self._require_member(actor, organization_id)
        return await self._load_company(organization_id, company_id)

    async def list_companies(self, actor: User, organization_id: OrganizationId) -> list[Company]:
        await self._require_member(actor, organization_id)
        return await self._companies.list_for_organization(organization_id)

    # -- commands ----------------------------------------------------------

    async def create_company(
        self,
        actor: User,
        organization_id: OrganizationId,
        name: str,
        slug: str,
        *,
        identity: dict[str, Any] | None = None,
        mission: str | None = None,
    ) -> Company:
        """Create a company in the organization (member required).

        Emits ``company.created``. New companies start in DRAFT status.
        """
        await self._require_member(actor, organization_id)
        existing = await self._companies.get_by_slug(organization_id, slug)
        if existing is not None:
            raise DuplicateSlug(
                f"a company with slug {slug!r} already exists in organization {organization_id}"
            )
        company = Company.create(
            organization_id=organization_id,
            name=name,
            slug=slug,
            identity=identity,
            mission=mission,
            now=_now(self._clock),
        )
        await self._companies.add(company)
        _publish(
            self._bus,
            "company.created",
            organization_id=organization_id,
            company_id=company.id,
            payload={"name": company.name, "slug": company.slug},
        )
        return company

    async def update_company(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        name: str | None = None,
        identity: dict[str, Any] | None = None,
        mission: str | None = None,
        strategy: dict[str, Any] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> Company:
        """Apply the provided field updates (member required).

        Emits ``company.updated`` with a name-only payload — identity,
        mission, strategy and configuration are business data and never
        mirrored into event payloads.
        """
        await self._require_member(actor, organization_id)
        company = await self._load_company(organization_id, company_id)
        if name is not None:
            company = company.rename(name)
        if identity is not None:
            company = company.update_identity(identity)
        if mission is not None:
            company = company.update_mission(mission)
        if strategy is not None:
            company = company.update_strategy(strategy)
        if configuration is not None:
            company = company.update_configuration(configuration)
        await self._companies.update(company)
        _publish(
            self._bus,
            "company.updated",
            organization_id=organization_id,
            company_id=company_id,
            payload={"name": company.name},
        )
        return company

    async def change_status(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        new_status: CompanyStatus,
    ) -> Company:
        """Transition the company status via the domain state machine.

        Emits ``company.status_changed`` with ``{"from": ..., "to": ...}``.
        Invalid transitions raise :class:`InvalidStatusTransition` and emit
        nothing.
        """
        await self._require_member(actor, organization_id)
        company = await self._load_company(organization_id, company_id)
        previous = company.status
        updated = company.transition_status(new_status)
        await self._companies.update(updated)
        _publish(
            self._bus,
            "company.status_changed",
            organization_id=organization_id,
            company_id=company_id,
            payload={"from": previous.value, "to": new_status.value},
        )
        return updated

    # -- internal ----------------------------------------------------------

    async def _require_member(self, actor: User, organization_id: OrganizationId) -> None:
        if await self._memberships.get(organization_id, actor.id) is None:
            raise NotAMember(f"user {actor.id} is not a member of organization {organization_id}")

    async def _load_company(
        self, organization_id: OrganizationId, company_id: CompanyId
    ) -> Company:
        company = await self._companies.get(organization_id, company_id)
        if company is None:
            raise CompanyNotFoundError(
                f"company {company_id} not found in organization {organization_id}"
            )
        return company
