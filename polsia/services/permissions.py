"""Permission and approval application services (Phase 2, Slice 4).

- :class:`PermissionService`: policy CRUD plus the pure permission-check
  engine (deny by default — dangerous operations default to deny). The check
  is a pure evaluation: no actor/user, no mutation, no events.
- :class:`ApprovalService`: the human-in-the-loop approval lifecycle for
  actions a policy marks ``approval_required``.

Company-scoped (ADR-0002): every actor-facing operation verifies the org
membership and the target company scope via the shared ``_CompanyScope``
helper. Events follow ADR-0004.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from typing import Any, Literal

from polsia.core.permissions.entities import Approval, Permission
from polsia.core.permissions.errors import ApprovalRequired, PermissionDenied
from polsia.core.permissions.ports import ApprovalRepository, PermissionRepository
from polsia.core.permissions.values import PermissionDecision
from polsia.core.shared.errors import NotFoundError, ValidationError
from polsia.core.shared.events import EventBus, EventEnvelope, EventStore, emit, utcnow
from polsia.core.shared.ids import ApprovalId, CompanyId, OrganizationId, UserId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.planning import _CompanyScope


def _most_specific(matches: list[Permission]) -> PermissionDecision:
    """Pick the winning rule from candidate matches.

    Most-specific match wins (resource_id match > wildcard — callers filter
    candidates to exact-or-wildcard already); among equal specificity the most
    recently created rule wins (consistent with upsert semantics).
    """
    exact = [m for m in matches if m.resource_id is not None]
    tier = exact or matches
    latest = max(tier, key=lambda m: (m.created_at, m.id))
    return latest.decision


class PermissionService:
    """Permission policy use cases + the pure permission-check engine."""

    def __init__(
        self,
        permissions: PermissionRepository,
        approvals: ApprovalRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        clock: Callable[[], dt.datetime] | None = None,
        store: EventStore | None = None,
    ) -> None:
        self._permissions = permissions
        # Retained so gates can reference existing pending approvals later.
        self._approvals = approvals
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus
        self._store = store
        self._clock = clock or utcnow

    async def require_company_access(
        self, actor: User, organization_id: object, company_id: object
    ) -> None:
        """Org membership + company scope check for pure-engine callers."""
        await self._scope.require_company(actor, organization_id, company_id)  # type: ignore[arg-type]

    async def set_policy(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        principal_type: str,
        principal_id: str | None = None,
        resource_type: str,
        resource_id: str | None = None,
        action: str,
        decision: PermissionDecision,
        constraints: dict[str, Any] | None = None,
    ) -> Permission:
        """Create or replace the policy rule matching (company, principal,
        resource, action). Emits ``permission.granted`` on allow and
        ``permission.revoked`` on deny."""
        await self._scope.require_company(actor, organization_id, company_id)
        now = self._clock()
        existing = await self._find_rule(
            company_id, principal_type, principal_id, resource_type, resource_id, action
        )
        if existing is not None:
            permission = dataclasses.replace(
                existing,
                decision=decision,
                constraints=dict(constraints or {}),
                updated_at=now,
            )
            await self._permissions.update(permission)
        else:
            permission = Permission.create(
                company_id=company_id,
                principal_type=principal_type,
                principal_id=principal_id,
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                decision=decision,
                constraints=constraints,
                now=now,
            )
            await self._permissions.add(permission)
        if decision is PermissionDecision.ALLOW:
            await self._publish(
                "permission.granted",
                organization_id=organization_id,
                company_id=company_id,
                permission=permission,
            )
        elif decision is PermissionDecision.DENY:
            await self._publish(
                "permission.revoked",
                organization_id=organization_id,
                company_id=company_id,
                permission=permission,
            )
        return permission

    async def list_policies(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId
    ) -> list[Permission]:
        await self._scope.require_company(actor, organization_id, company_id)
        return await self._permissions.list_for_company(company_id)

    async def check(
        self,
        principal_type: str,
        principal_id: str | None,
        company_id: CompanyId,
        resource_type: str,
        resource_id: str | None,
        action: str,
        *,
        default_decision: PermissionDecision = PermissionDecision.DENY,
    ) -> PermissionDecision:
        """Pure engine evaluation — no actor/user, no mutation, no events.

        Rules:
        1. Rules for the exact principal win; company-wide defaults
           (``principal_id is None``) are the fallback.
        2. Within a tier, the most-specific resource match wins
           (resource_id match > wildcard).
        3. No rule at all -> ``default_decision`` (deny by default —
           dangerous ops default to deny).
        """
        for principal in (principal_id, None):
            rules = await self._permissions.list_for_principal(
                company_id, principal_type, principal
            )
            candidates = [
                r
                for r in rules
                if r.resource_type == resource_type
                and r.action == action
                and (r.resource_id == resource_id or r.resource_id is None)
            ]
            if candidates:
                return _most_specific(candidates)
        return default_decision

    async def require_approval_for(
        self,
        principal_type: str,
        principal_id: str | None,
        company_id: CompanyId,
        resource_type: str,
        resource_id: str | None,
        action: str,
    ) -> None:
        """Gate helper for callers (e.g. agent runtimes).

        Returns when allowed; raises :class:`PermissionDenied` on deny and
        :class:`ApprovalRequired` on ``approval_required``. Callers catch
        :class:`ApprovalRequired` and create the approval via
        :class:`ApprovalService` (the approval id is attached afterwards).
        """
        decision = await self.check(
            principal_type, principal_id, company_id, resource_type, resource_id, action
        )
        if decision is PermissionDecision.ALLOW:
            return
        subject = f"{principal_type} {principal_id or '*'}"
        target = f"{resource_type} {resource_id or '*'}"
        if decision is PermissionDecision.DENY:
            raise PermissionDenied(
                f"{subject} may not {action} on {target} in company {company_id}"
            )
        raise ApprovalRequired(
            f"{subject} needs approval to {action} on {target} in company {company_id}"
        )

    async def _find_rule(
        self,
        company_id: CompanyId,
        principal_type: str,
        principal_id: str | None,
        resource_type: str,
        resource_id: str | None,
        action: str,
    ) -> Permission | None:
        rules = await self._permissions.list_for_principal(company_id, principal_type, principal_id)
        for rule in rules:
            if (
                rule.resource_type == resource_type
                and rule.resource_id == resource_id
                and rule.action == action
            ):
                return rule
        return None

    async def _publish(
        self,
        event_type: str,
        *,
        organization_id: OrganizationId,
        company_id: CompanyId,
        permission: Permission,
    ) -> None:
        await emit(
            self._bus,
            self._store,
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload={
                    "permission_id": str(permission.id),
                    "principal_type": permission.principal_type,
                    "principal_id": permission.principal_id,
                    "resource_type": permission.resource_type,
                    "resource_id": permission.resource_id,
                    "action": permission.action,
                    "decision": permission.decision.value,
                },
            ),
        )


class ApprovalService:
    """Human-in-the-loop approval lifecycle (request / decide / list)."""

    def __init__(
        self,
        approvals: ApprovalRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        clock: Callable[[], dt.datetime] | None = None,
        store: EventStore | None = None,
    ) -> None:
        self._approvals = approvals
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus
        self._store = store
        self._clock = clock or utcnow

    async def request_approval(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        requested_by: UserId,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        reason: str | None = None,
        expires_at: dt.datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Approval:
        """Create a pending approval. Emits ``approval.requested``."""
        await self._scope.require_company(actor, organization_id, company_id)
        approval = Approval.create(
            company_id=company_id,
            requested_by=requested_by,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            reason=reason,
            expires_at=expires_at,
            metadata=metadata,
            now=self._clock(),
        )
        await self._approvals.add(approval)
        await self._publish(
            "approval.requested",
            organization_id=organization_id,
            company_id=company_id,
            approval=approval,
        )
        return approval

    async def decide_approval(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        approval_id: ApprovalId,
        *,
        decision: Literal["approved", "rejected"],
    ) -> Approval:
        """Decide a pending approval (approve/reject). Expired or already
        decided approvals raise validation/domain-rule errors. Emits
        ``approval.decided``."""
        await self._scope.require_company(actor, organization_id, company_id)
        approval = await self._approvals.get(company_id, approval_id)
        if approval is None:
            raise NotFoundError(f"approval {approval_id} not found")
        now = self._clock()
        if approval.is_expired(now):
            raise ValidationError(
                f"approval {approval_id} has expired and can no longer be decided"
            )
        if decision == "approved":
            approval = approval.approve(actor.id, now=now)
        else:
            approval = approval.reject(actor.id, now=now)
        await self._approvals.update(approval)
        await self._publish(
            "approval.decided",
            organization_id=organization_id,
            company_id=company_id,
            approval=approval,
            extra={"decided_by": str(actor.id), "decision": approval.status.value},
        )
        return approval

    async def list_approvals(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        pending_only: bool = False,
    ) -> list[Approval]:
        await self._scope.require_company(actor, organization_id, company_id)
        if pending_only:
            return await self._approvals.list_pending(company_id)
        return await self._approvals.list_for_company(company_id)

    async def _publish(
        self,
        event_type: str,
        *,
        organization_id: OrganizationId,
        company_id: CompanyId,
        approval: Approval,
        extra: dict[str, Any] | None = None,
    ) -> None:
        await emit(
            self._bus,
            self._store,
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload={
                    "approval_id": str(approval.id),
                    "requested_by": str(approval.requested_by),
                    "action": approval.action,
                    "resource_type": approval.resource_type,
                    "resource_id": approval.resource_id,
                    "status": approval.status.value,
                    **(extra or {}),
                },
            ),
        )
