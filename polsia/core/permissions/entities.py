"""Permission and approval domain entities (company-scoped, frozen dataclasses)."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from polsia.core.permissions.values import (
    APPROVAL_STATUS_TRANSITIONS,
    ApprovalStatus,
    PermissionDecision,
)
from polsia.core.shared.errors import InvalidStatusTransition, ValidationError
from polsia.core.shared.events import utcnow
from polsia.core.shared.ids import ApprovalId, CompanyId, PermissionId, UserId


def _require_non_empty(value: str, label: str) -> str:
    if not value.strip():
        raise ValidationError(f"{label} must not be empty")
    return value.strip()


def _require_optional_non_empty(value: str | None, label: str) -> str | None:
    if value is not None and not value.strip():
        raise ValidationError(f"{label} must not be empty when provided")
    return value


@dataclasses.dataclass(frozen=True)
class Permission:
    """A permission policy rule: principal x resource x action -> decision.

    ``principal_type`` is a free-form string (e.g. ``"agent"``, ``"role"``);
    ``principal_id`` is the agent id or role name, or ``None`` for the
    company-wide default. ``resource_id`` is the concrete resource (e.g.
    ``tool name@version``) or ``None`` for a wildcard. ``constraints`` is an
    arbitrary JSON payload — the core never interprets it.
    """

    id: PermissionId
    company_id: CompanyId
    principal_type: str
    principal_id: str | None
    resource_type: str
    resource_id: str | None
    action: str
    decision: PermissionDecision
    constraints: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        company_id: CompanyId,
        principal_type: str,
        principal_id: str | None = None,
        resource_type: str,
        resource_id: str | None = None,
        action: str,
        decision: PermissionDecision,
        constraints: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> Permission:
        ts = now or utcnow()
        return Permission(
            id=PermissionId.generate(),
            company_id=company_id,
            principal_type=_require_non_empty(principal_type, "principal_type"),
            principal_id=_require_optional_non_empty(principal_id, "principal_id"),
            resource_type=_require_non_empty(resource_type, "resource_type"),
            resource_id=_require_optional_non_empty(resource_id, "resource_id"),
            action=_require_non_empty(action, "action"),
            decision=decision,
            constraints=dict(constraints or {}),
            created_at=ts,
            updated_at=ts,
        )


@dataclasses.dataclass(frozen=True)
class Approval:
    """A human-in-the-loop approval request (company-scoped).

    Created when the permission engine returns ``approval_required`` for a
    dangerous action; a human decides it (approve/reject). All post-pending
    states are terminal.
    """

    id: ApprovalId
    company_id: CompanyId
    requested_by: UserId
    action: str
    resource_type: str
    resource_id: str | None = None
    reason: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    expires_at: dt.datetime | None = None
    decided_by: UserId | None = None
    decided_at: dt.datetime | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        company_id: CompanyId,
        requested_by: UserId,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        reason: str | None = None,
        expires_at: dt.datetime | None = None,
        metadata: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> Approval:
        ts = now or utcnow()
        return Approval(
            id=ApprovalId.generate(),
            company_id=company_id,
            requested_by=requested_by,
            action=_require_non_empty(action, "action"),
            resource_type=_require_non_empty(resource_type, "resource_type"),
            resource_id=_require_optional_non_empty(resource_id, "resource_id"),
            reason=reason,
            status=ApprovalStatus.PENDING,
            expires_at=expires_at,
            decided_by=None,
            decided_at=None,
            metadata=dict(metadata or {}),
            created_at=ts,
            updated_at=ts,
        )

    def _decided(self, status: ApprovalStatus, decided_by: UserId, now: dt.datetime) -> Approval:
        allowed = APPROVAL_STATUS_TRANSITIONS[self.status]
        if status not in allowed:
            raise InvalidStatusTransition(
                f"approval {self.status.value!r} cannot transition to {status.value!r} "
                "(terminal state)"
            )
        return dataclasses.replace(
            self, status=status, decided_by=decided_by, decided_at=now, updated_at=now
        )

    def approve(self, decided_by: UserId, now: dt.datetime | None = None) -> Approval:
        """Pending -> approved (any other state is an invalid transition)."""
        return self._decided(ApprovalStatus.APPROVED, decided_by, now or utcnow())

    def reject(self, decided_by: UserId, now: dt.datetime | None = None) -> Approval:
        """Pending -> rejected (any other state is an invalid transition)."""
        return self._decided(ApprovalStatus.REJECTED, decided_by, now or utcnow())

    def is_expired(self, now: dt.datetime) -> bool:
        """A pending approval past its ``expires_at`` is expired."""
        return (
            self.status is ApprovalStatus.PENDING
            and self.expires_at is not None
            and now > self.expires_at
        )
