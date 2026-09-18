"""Usage endpoints, nested under organization + company (ADR-0002 scoping).

Read-only surface over :class:`polsia.services.usage.UsageService`; records
are written by the orchestration flow.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from polsia.api.deps import (
    CurrentUser,
    UsageServiceDep,
    parse_company_id,
    parse_organization_id,
)
from polsia.api.schemas import UsageOut
from polsia.core.shared.ids import TaskExecutionId

router = APIRouter(prefix="/organizations/{org_id}/companies/{company_id}/usage", tags=["usage"])


@router.get("", response_model=list[UsageOut])
async def list_usage(
    org_id: str,
    company_id: str,
    user: CurrentUser,
    service: UsageServiceDep,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[UsageOut]:
    """List the company's usage records (membership required)."""
    records = await service.list_usage(
        user, parse_organization_id(org_id), parse_company_id(company_id), limit=limit
    )
    return [UsageOut.model_validate(r) for r in records]


@router.get("/executions/{execution_id}", response_model=list[UsageOut])
async def usage_for_execution(
    org_id: str,
    company_id: str,
    execution_id: str,
    user: CurrentUser,
    service: UsageServiceDep,
) -> list[UsageOut]:
    """List the usage records of one execution (membership required)."""
    records = await service.usage_for_execution(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskExecutionId.parse(execution_id),
    )
    return [UsageOut.model_validate(r) for r in records]
