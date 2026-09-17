"""Organization endpoints.

Thin HTTP surface over :class:`polsia.services.tenancy.OrganizationService`:
membership/role checks live in the service, not here.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from polsia.api.deps import (
    CurrentUser,
    OrganizationServiceDep,
    parse_organization_id,
    parse_user_id,
)
from polsia.api.schemas import (
    MemberAdd,
    MemberOut,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=OrganizationOut)
async def create_organization(
    payload: OrganizationCreate, user: CurrentUser, service: OrganizationServiceDep
) -> OrganizationOut:
    """Create an organization owned by the authenticated user."""
    organization = await service.create_organization(user, payload.name, payload.slug)
    return OrganizationOut.model_validate(organization)


@router.get("", response_model=list[OrganizationOut])
async def list_organizations(
    user: CurrentUser, service: OrganizationServiceDep
) -> list[OrganizationOut]:
    """List the organizations the authenticated user owns or belongs to."""
    organizations = await service.list_organizations_for_user(user)
    return [OrganizationOut.model_validate(organization) for organization in organizations]


@router.get("/{org_id}", response_model=OrganizationOut)
async def get_organization(
    org_id: str, user: CurrentUser, service: OrganizationServiceDep
) -> OrganizationOut:
    """Fetch one organization (membership required)."""
    organization = await service.get_organization_for_user(parse_organization_id(org_id), user)
    return OrganizationOut.model_validate(organization)


@router.patch("/{org_id}", response_model=OrganizationOut)
async def rename_organization(
    org_id: str,
    payload: OrganizationUpdate,
    user: CurrentUser,
    service: OrganizationServiceDep,
) -> OrganizationOut:
    """Rename the organization (admin role required)."""
    organization = await service.rename(user, parse_organization_id(org_id), payload.name)
    return OrganizationOut.model_validate(organization)


@router.post("/{org_id}/members", status_code=status.HTTP_201_CREATED, response_model=MemberOut)
async def add_member(
    org_id: str, payload: MemberAdd, user: CurrentUser, service: OrganizationServiceDep
) -> MemberOut:
    """Add a member to the organization (admin role required)."""
    membership = await service.add_member(
        user, parse_organization_id(org_id), parse_user_id(payload.user_id), payload.role
    )
    return MemberOut.model_validate(membership)


@router.get("/{org_id}/members", response_model=list[MemberOut])
async def list_members(
    org_id: str, user: CurrentUser, service: OrganizationServiceDep
) -> list[MemberOut]:
    """List the organization's members (membership required)."""
    members = await service.list_members(user, parse_organization_id(org_id))
    return [MemberOut.model_validate(member) for member in members]
