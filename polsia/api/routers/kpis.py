"""KPI endpoints, nested under organization + company (ADR-0002 scoping).

Thin HTTP surface over :class:`polsia.services.planning.KpiService`.
KPI status is evaluated deterministically in the domain — never by AI.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from polsia.api.deps import CurrentUser, KpiServiceDep, parse_company_id, parse_organization_id
from polsia.api.schemas import KpiCreate, KpiMeasurement, KpiOut, KpiUpdate
from polsia.core.shared.ids import GoalId, KpiId

router = APIRouter(prefix="/organizations/{org_id}/companies/{company_id}/kpis", tags=["kpis"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=KpiOut)
async def create_kpi(
    org_id: str, company_id: str, payload: KpiCreate, user: CurrentUser, service: KpiServiceDep
) -> KpiOut:
    """Create a KPI in the company, optionally attached to a goal."""
    kpi = await service.create_kpi(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        name=payload.name,
        direction=payload.direction,
        target_value=payload.target_value,
        goal_id=GoalId.parse(payload.goal_id) if payload.goal_id else None,
        description=payload.description,
        unit=payload.unit,
        metadata=payload.metadata,
    )
    return KpiOut.model_validate(kpi)


@router.get("", response_model=list[KpiOut])
async def list_kpis(
    org_id: str,
    company_id: str,
    user: CurrentUser,
    service: KpiServiceDep,
    goal_id: str | None = Query(default=None),
) -> list[KpiOut]:
    """List the company's KPIs, optionally filtered by goal."""
    kpis = await service.list_kpis(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        goal_id=GoalId.parse(goal_id) if goal_id else None,
    )
    return [KpiOut.model_validate(kpi) for kpi in kpis]


@router.get("/{kpi_id}", response_model=KpiOut)
async def get_kpi(
    org_id: str, company_id: str, kpi_id: str, user: CurrentUser, service: KpiServiceDep
) -> KpiOut:
    """Fetch one KPI (membership required; company-scoped lookup)."""
    kpi = await service.get_kpi(
        user, parse_organization_id(org_id), parse_company_id(company_id), KpiId.parse(kpi_id)
    )
    return KpiOut.model_validate(kpi)


@router.patch("/{kpi_id}", response_model=KpiOut)
async def update_kpi(
    org_id: str,
    company_id: str,
    kpi_id: str,
    payload: KpiUpdate,
    user: CurrentUser,
    service: KpiServiceDep,
) -> KpiOut:
    """Partially update a KPI definition (membership required)."""
    updates = payload.model_dump(exclude_unset=True)
    kpi = await service.update_kpi(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        KpiId.parse(kpi_id),
        name=updates.get("name"),
        description=updates.get("description"),
        target_value=updates.get("target_value"),
        direction=updates.get("direction"),
        unit=updates.get("unit"),
        metadata=updates.get("metadata"),
    )
    return KpiOut.model_validate(kpi)


@router.post("/{kpi_id}/measurements", response_model=KpiOut)
async def record_measurement(
    org_id: str,
    company_id: str,
    kpi_id: str,
    payload: KpiMeasurement,
    user: CurrentUser,
    service: KpiServiceDep,
) -> KpiOut:
    """Record a measurement; the KPI status is re-derived deterministically."""
    kpi = await service.record_measurement(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        KpiId.parse(kpi_id),
        current_value=payload.current_value,
    )
    return KpiOut.model_validate(kpi)
