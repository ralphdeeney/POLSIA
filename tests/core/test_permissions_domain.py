"""Permission/approval domain tests (pure entities — no DB, no services)."""

from __future__ import annotations

import datetime as dt

import pytest
from polsia.core.permissions.entities import Approval, Permission
from polsia.core.permissions.errors import ApprovalRequired, PermissionDenied
from polsia.core.permissions.values import (
    APPROVAL_STATUS_TRANSITIONS,
    ApprovalStatus,
    PermissionDecision,
)
from polsia.core.shared.errors import InvalidStatusTransition, ValidationError
from polsia.core.shared.ids import CompanyId, UserId

COMPANY = CompanyId.generate()
USER = UserId.generate()


def make_permission(**overrides: object) -> Permission:
    kwargs: dict = {
        "company_id": COMPANY,
        "principal_type": "agent",
        "principal_id": "agt_01TEST",
        "resource_type": "tool",
        "resource_id": "web.search@1.0.0",
        "action": "web.search",
        "decision": PermissionDecision.ALLOW,
    }
    kwargs.update(overrides)
    return Permission.create(**kwargs)


def make_approval(**overrides: object) -> Approval:
    kwargs: dict = {
        "company_id": COMPANY,
        "requested_by": USER,
        "action": "web.search",
        "resource_type": "tool",
        "resource_id": "web.search@1.0.0",
    }
    kwargs.update(overrides)
    return Approval.create(**kwargs)


class TestPermissionDecisionValues:
    def test_enum_values(self) -> None:
        assert PermissionDecision.ALLOW == "allow"
        assert PermissionDecision.DENY == "deny"
        assert PermissionDecision.APPROVAL_REQUIRED == "approval_required"

    def test_approval_status_values(self) -> None:
        assert ApprovalStatus.PENDING == "pending"
        assert ApprovalStatus.APPROVED == "approved"
        assert ApprovalStatus.REJECTED == "rejected"
        assert ApprovalStatus.EXPIRED == "expired"

    def test_transitions_terminal_after_pending(self) -> None:
        assert ApprovalStatus.APPROVED in APPROVAL_STATUS_TRANSITIONS[ApprovalStatus.PENDING]
        assert ApprovalStatus.REJECTED in APPROVAL_STATUS_TRANSITIONS[ApprovalStatus.PENDING]
        assert ApprovalStatus.EXPIRED in APPROVAL_STATUS_TRANSITIONS[ApprovalStatus.PENDING]
        assert APPROVAL_STATUS_TRANSITIONS[ApprovalStatus.APPROVED] == frozenset()
        assert APPROVAL_STATUS_TRANSITIONS[ApprovalStatus.REJECTED] == frozenset()
        assert APPROVAL_STATUS_TRANSITIONS[ApprovalStatus.EXPIRED] == frozenset()


class TestPermissionEntity:
    def test_create_defaults(self) -> None:
        permission = make_permission(principal_id=None, resource_id=None)
        assert permission.id.startswith("prm_")
        assert permission.principal_id is None
        assert permission.resource_id is None
        assert permission.constraints == {}
        assert permission.created_at.tzinfo is not None

    def test_create_strips_and_keeps_fields(self) -> None:
        permission = make_permission(principal_type="  agent  ", action="  web.search  ")
        assert permission.principal_type == "agent"
        assert permission.action == "web.search"

    @pytest.mark.parametrize("field", ["principal_type", "resource_type", "action"])
    def test_create_rejects_empty_required_fields(self, field: str) -> None:
        with pytest.raises(ValidationError):
            make_permission(**{field: "   "})

    @pytest.mark.parametrize("field", ["principal_id", "resource_id"])
    def test_create_rejects_empty_optional_fields(self, field: str) -> None:
        with pytest.raises(ValidationError):
            make_permission(**{field: ""})


class TestApprovalEntity:
    def test_create_defaults_to_pending(self) -> None:
        approval = make_approval()
        assert approval.id.startswith("apr_")
        assert approval.status is ApprovalStatus.PENDING
        assert approval.decided_by is None
        assert approval.decided_at is None
        assert approval.expires_at is None

    def test_approve_from_pending(self) -> None:
        now = dt.datetime(2026, 9, 16, 12, 0, tzinfo=dt.UTC)
        approval = make_approval().approve(USER, now=now)
        assert approval.status is ApprovalStatus.APPROVED
        assert approval.decided_by == USER
        assert approval.decided_at == now

    def test_reject_from_pending(self) -> None:
        now = dt.datetime(2026, 9, 16, 12, 0, tzinfo=dt.UTC)
        approval = make_approval().reject(USER, now=now)
        assert approval.status is ApprovalStatus.REJECTED
        assert approval.decided_by == USER

    def test_approve_is_terminal(self) -> None:
        approved = make_approval().approve(USER)
        with pytest.raises(InvalidStatusTransition):
            approved.approve(USER)
        with pytest.raises(InvalidStatusTransition):
            approved.reject(USER)

    def test_reject_is_terminal(self) -> None:
        rejected = make_approval().reject(USER)
        with pytest.raises(InvalidStatusTransition):
            rejected.approve(USER)

    def test_is_expired_with_fixed_clock(self) -> None:
        expires_at = dt.datetime(2026, 9, 16, 12, 0, tzinfo=dt.UTC)
        approval = make_approval(expires_at=expires_at)
        before = dt.datetime(2026, 9, 16, 11, 59, tzinfo=dt.UTC)
        after = dt.datetime(2026, 9, 16, 12, 1, tzinfo=dt.UTC)
        assert approval.is_expired(before) is False
        assert approval.is_expired(after) is True

    def test_is_expired_false_without_expires_at(self) -> None:
        assert make_approval().is_expired(dt.datetime(2030, 1, 1, tzinfo=dt.UTC)) is False

    def test_is_expired_false_when_decided(self) -> None:
        expires_at = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
        approved = make_approval(expires_at=expires_at).approve(USER, now=expires_at)
        assert approved.is_expired(dt.datetime(2030, 1, 1, tzinfo=dt.UTC)) is False


class TestPermissionErrors:
    def test_permission_denied_mapping(self) -> None:
        assert PermissionDenied.status_code == 403
        assert PermissionDenied.code == "permission_denied"

    def test_approval_required_mapping_and_approval_id(self) -> None:
        assert ApprovalRequired.status_code == 403
        assert ApprovalRequired.code == "approval_required"
        err = ApprovalRequired("needs approval", approval_id="apr_01TEST")
        assert err.approval_id == "apr_01TEST"
        assert ApprovalRequired("needs approval").approval_id is None
