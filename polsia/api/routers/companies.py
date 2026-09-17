"""Company endpoints, nested under their organization.

Thin HTTP surface over :class:`polsia.services.tenancy.CompanyService`:
membership checks and tenant scoping live in the service (ADR-0002).
"""

from __future__ import annotations

from fastapi import APIRouter, status

from polsia.api.deps import (
    CompanyServiceDep,
    CurrentUser,
    parse_company_id,
    parse_organization_id,
)
from polsia.api.schemas import (
    CompanyCreate,
    CompanyOut,
    CompanyStatusChange,
    CompanyUpdate,
)

router = APIRouter(prefix="/organizations/{org_id}/companies", tags=["companies"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CompanyOut)
async def create_company(
    org_id: str, payload: CompanyCreate, user: CurrentUser, service: CompanyServiceDep
) -> CompanyOut:
    """Create a company inside the organization (membership required)."""
    company = await service.create_company(
        user,
        parse_organization_id(org_id),
        payload.name,
        payload.slug,
        identity=payload.identity,
        mission=payload.mission,
    )
    return CompanyOut.model_validate(company)


@router.get("", response_model=list[CompanyOut])
async def list_companies(
    org_id: str, user: CurrentUser, service: CompanyServiceDep
) -> list[CompanyOut]:
    """List the organization's companies (membership required)."""
    companies = await service.list_companies(user, parse_organization_id(org_id))
    return [CompanyOut.model_validate(company) for company in companies]


@router.get("/{company_id}", response_model=CompanyOut)
async def get_company(
    org_id: str, company_id: str, user: CurrentUser, service: CompanyServiceDep
) -> CompanyOut:
    """Fetch one company (membership required; org-scoped lookup)."""
    company = await service.get_company(
        user, parse_organization_id(org_id), parse_company_id(company_id)
    )
    return CompanyOut.model_validate(company)


@router.patch("/{company_id}", response_model=CompanyOut)
async def update_company(
    org_id: str,
    company_id: str,
    payload: CompanyUpdate,
    user: CurrentUser,
    service: CompanyServiceDep,
) -> CompanyOut:
    """Partially update a company (membership required)."""
    updates = payload.model_dump(exclude_unset=True)
    company = await service.update_company(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        name=updates.get("name"),
        identity=updates.get("identity"),
        mission=updates.get("mission"),
        strategy=updates.get("strategy"),
        configuration=updates.get("configuration"),
    )
    return CompanyOut.model_validate(company)


@router.post("/{company_id}/status", response_model=CompanyOut)
async def change_company_status(
    org_id: str,
    company_id: str,
    payload: CompanyStatusChange,
    user: CurrentUser,
    service: CompanyServiceDep,
) -> CompanyOut:
    """Transition the company status (invalid transitions -> 422 problem)."""
    company = await service.change_status(
        user, parse_organization_id(org_id), parse_company_id(company_id), payload.status
    )
    return CompanyOut.model_validate(company)
