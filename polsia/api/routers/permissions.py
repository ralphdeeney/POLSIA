"""Permission and approval endpoints (Phase 2, Slice 4).

Company-scoped under /organizations/{org_id}/companies/{company_id}:

- ``PUT /permissions``: set (or replace) a policy rule (org member required).
- ``GET /permissions``: list policy rules (org member required).
- ``POST /permissions/check``: pure permission-engine evaluation — returns
  the decision and nothing else (no mutation, no events).
- ``POST /approvals``: request a human-in-the-loop approval.
- ``GET /approvals``: list approvals (optionally pending only).
- ``POST /approvals/{approval_id}/decide``: approve/reject a pending approval.

Thin HTTP surface over :class:`polsia.services.permissions.PermissionService`
and :class:`polsia.services.permissions.ApprovalService`. All authorization
(org membership + company scope) happens inside the services.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from polsia.api.deps import (
    ApprovalServiceDep,
    CurrentUser,
    PermissionServiceDep,
    parse_company_id,
    parse_organization_id,
    parse_user_id,
)
from polsia.api.schemas import (
    ApprovalCreate,
    ApprovalDecide,
    ApprovalOut,
    PermissionCheck,
    PermissionOut,
    PermissionSet,
)
from polsia.core.shared.errors import NotFoundError
from polsia.core.shared.ids import ApprovalId

permissions_router = APIRouter(
    prefix="/organizations/{org_id}/companies/{company_id}", tags=["permissions"]
)


# -- Permission policies -------------------------------------------------------


@permissions_router.put("/permissions", response_model=PermissionOut)
async def set_policy(
    org_id: str,
    company_id: str,
    payload: PermissionSet,
    user: CurrentUser,
    service: PermissionServiceDep,
) -> PermissionOut:
    """Set (or replace) a permission policy rule (UPSERT semantics)."""
    permission = await service.set_policy(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        principal_type=payload.principal_type,
        principal_id=payload.principal_id,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        action=payload.action,
        decision=payload.decision,
        constraints=payload.constraints,
    )
    return PermissionOut.model_validate(permission)


@permissions_router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(
    org_id: str, company_id: str, user: CurrentUser, service: PermissionServiceDep
) -> list[PermissionOut]:
    permissions = await service.list_policies(
        user, parse_organization_id(org_id), parse_company_id(company_id)
    )
    return [PermissionOut.model_validate(p) for p in permissions]


@permissions_router.post("/permissions/check")
async def check_permission(
    org_id: str,
    company_id: str,
    payload: PermissionCheck,
    user: CurrentUser,
    service: PermissionServiceDep,
) -> dict[str, str]:
    """Pure engine evaluation — returns the decision only. Membership is
    enforced first so policy configuration cannot be probed cross-tenant."""
    await service.require_company_access(
        user, parse_organization_id(org_id), parse_company_id(company_id)
    )
    decision = await service.check(
        principal_type=payload.principal_type,
        principal_id=payload.principal_id,
        company_id=parse_company_id(company_id),
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        action=payload.action,
    )
    return {"decision": decision.value}


# -- Approvals (human-in-the-loop) ------------------------------------------------


@permissions_router.post(
    "/approvals", status_code=status.HTTP_201_CREATED, response_model=ApprovalOut
)
async def request_approval(
    org_id: str,
    company_id: str,
    payload: ApprovalCreate,
    user: CurrentUser,
    service: ApprovalServiceDep,
) -> ApprovalOut:
    """Create a pending approval (expires_at optional)."""
    approval = await service.request_approval(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        requested_by=parse_user_id(payload.requested_by),
        action=payload.action,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        reason=payload.reason,
        expires_at=payload.expires_at,
        metadata=payload.metadata,
    )
    return ApprovalOut.model_validate(approval)


@permissions_router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    org_id: str,
    company_id: str,
    user: CurrentUser,
    service: ApprovalServiceDep,
    pending_only: bool = False,
) -> list[ApprovalOut]:
    approvals = await service.list_approvals(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        pending_only=pending_only,
    )
    return [ApprovalOut.model_validate(a) for a in approvals]


@permissions_router.post("/approvals/{approval_id}/decide", response_model=ApprovalOut)
async def decide_approval(
    org_id: str,
    company_id: str,
    approval_id: str,
    payload: ApprovalDecide,
    user: CurrentUser,
    service: ApprovalServiceDep,
) -> ApprovalOut:
    """Approve or reject a pending approval (invalid transitions -> 422)."""
    approval = await service.decide_approval(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        _approval_id(approval_id),
        decision=payload.decision,
    )
    return ApprovalOut.model_validate(approval)


def _approval_id(raw: str) -> ApprovalId:
    try:
        return ApprovalId.parse(raw)
    except ValueError as exc:
        raise NotFoundError(f"approval {raw!r} not found") from exc
