"""Value objects for the permissions domain."""

from __future__ import annotations

import enum


class PermissionDecision(enum.StrEnum):
    """Engine decision for a (principal, resource, action) check."""

    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class ApprovalStatus(enum.StrEnum):
    """Lifecycle status of a human-in-the-loop approval."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


#: Allowed approval transitions. All post-pending states are terminal.
APPROVAL_STATUS_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset(
        {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED}
    ),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.REJECTED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
}
