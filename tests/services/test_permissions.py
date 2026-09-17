"""PermissionService / ApprovalService tests with in-memory fakes.

Covers policy upsert semantics, pure engine evaluation (most-specific-wins,
default deny), the approval lifecycle, and event emission (types + payloads).
"""

from __future__ import annotations

import datetime as dt

import pytest
from polsia.core.permissions.entities import Approval, Permission
from polsia.core.permissions.errors import ApprovalRequired, PermissionDenied
from polsia.core.permissions.values import ApprovalStatus, PermissionDecision
from polsia.core.shared.errors import InvalidStatusTransition, NotAMember, ValidationError
from polsia.core.shared.events import EventEnvelope, EventHandler
from polsia.core.shared.ids import ApprovalId, CompanyId, OrganizationId, PermissionId, UserId
from polsia.core.tenancy.entities import Company, OrgMembership, User
from polsia.core.tenancy.values import MembershipRole
from polsia.services.permissions import ApprovalService, PermissionService

# -- In-memory fakes -----------------------------------------------------------


class RecordingBus:
    """Fake EventBus: records every published event and dispatches to
    subscribers matching the type prefix (or the wildcard "*")."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []
        self._handlers: list[tuple[str, EventHandler]] = []

    def subscribe(self, pattern: str, handler: EventHandler) -> None:
        self._handlers.append((pattern, handler))

    def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)
        for pattern, handler in self._handlers:
            if pattern == "*" or event.type.startswith(pattern):
                handler(event)


class FakePermissionRepository:
    def __init__(self) -> None:
        self.by_id: dict[str, Permission] = {}

    async def add(self, permission: Permission) -> None:
        self.by_id[str(permission.id)] = permission

    async def get(self, company_id: CompanyId, permission_id: PermissionId) -> Permission | None:
        permission = self.by_id.get(str(permission_id))
        if permission is not None and permission.company_id == company_id:
            return permission
        return None

    async def list_for_company(self, company_id: CompanyId) -> list[Permission]:
        return [p for p in self.by_id.values() if p.company_id == company_id]

    async def list_for_principal(
        self, company_id: CompanyId, principal_type: str, principal_id: str | None
    ) -> list[Permission]:
        return [
            p
            for p in self.by_id.values()
            if p.company_id == company_id
            and p.principal_type == principal_type
            and p.principal_id == principal_id
        ]

    async def update(self, permission: Permission) -> None:
        self.by_id[str(permission.id)] = permission


class FakeApprovalRepository:
    def __init__(self) -> None:
        self.by_id: dict[str, Approval] = {}

    async def add(self, approval: Approval) -> None:
        self.by_id[str(approval.id)] = approval

    async def get(self, company_id: CompanyId, approval_id: ApprovalId) -> Approval | None:
        approval = self.by_id.get(str(approval_id))
        if approval is not None and approval.company_id == company_id:
            return approval
        return None

    async def list_for_company(self, company_id: CompanyId) -> list[Approval]:
        return [a for a in self.by_id.values() if a.company_id == company_id]

    async def list_pending(self, company_id: CompanyId) -> list[Approval]:
        return [
            a
            for a in self.by_id.values()
            if a.company_id == company_id and a.status is ApprovalStatus.PENDING
        ]

    async def update(self, approval: Approval) -> None:
        self.by_id[str(approval.id)] = approval


class FakeCompanyRepository:
    def __init__(self) -> None:
        self.by_id: dict[CompanyId, Company] = {}

    async def add(self, company: Company) -> None:
        self.by_id[company.id] = company

    async def get(self, organization_id: OrganizationId, company_id: CompanyId) -> Company | None:
        company = self.by_id.get(company_id)
        if company is not None and company.organization_id == organization_id:
            return company
        return None

    async def get_by_slug(self, organization_id: OrganizationId, slug: str) -> Company | None:
        for company in self.by_id.values():
            if company.organization_id == organization_id and company.slug == slug:
                return company
        return None

    async def list_for_organization(self, organization_id: OrganizationId) -> list[Company]:
        return [c for c in self.by_id.values() if c.organization_id == organization_id]

    async def update(self, company: Company) -> None:
        self.by_id[company.id] = company


class FakeMembershipRepository:
    def __init__(self) -> None:
        self.by_key: dict[tuple[OrganizationId, UserId], OrgMembership] = {}

    async def add(self, membership: OrgMembership) -> None:
        self.by_key[(membership.organization_id, membership.user_id)] = membership

    async def get(self, organization_id: OrganizationId, user_id: UserId) -> OrgMembership | None:
        return self.by_key.get((organization_id, user_id))

    async def list_for_organization(self, organization_id: OrganizationId) -> list[OrgMembership]:
        return [m for m in self.by_key.values() if m.organization_id == organization_id]

    async def update(self, membership: OrgMembership) -> None:
        self.by_key[(membership.organization_id, membership.user_id)] = membership


# -- Fixtures / helpers ----------------------------------------------------------


class World:
    def __init__(self) -> None:
        self.bus = RecordingBus()
        self.permissions = FakePermissionRepository()
        self.approvals = FakeApprovalRepository()
        self.companies = FakeCompanyRepository()
        self.memberships = FakeMembershipRepository()
        self.permission_service = PermissionService(
            permissions=self.permissions,
            approvals=self.approvals,
            companies=self.companies,
            memberships=self.memberships,
            bus=self.bus,
        )
        self.approval_service = ApprovalService(
            approvals=self.approvals,
            companies=self.companies,
            memberships=self.memberships,
            bus=self.bus,
        )


async def make_member(world: World) -> tuple[User, OrganizationId, CompanyId]:
    user = User.register(email="owner@example.com", hashed_password="x", display_name="Owner")
    organization_id = OrganizationId.generate()
    company = Company.create(organization_id=organization_id, name="Co", slug="co-1")
    await world.companies.add(company)
    await world.memberships.add(
        OrgMembership.create(
            organization_id=organization_id, user_id=user.id, role=MembershipRole.OWNER
        )
    )
    return user, organization_id, company.id


def set_policy_kwargs(**overrides: object) -> dict:
    kwargs: dict = {
        "principal_type": "agent",
        "principal_id": "agt_01AGENT",
        "resource_type": "tool",
        "resource_id": "web.search@1.0.0",
        "action": "web.search",
        "decision": PermissionDecision.ALLOW,
    }
    kwargs.update(overrides)
    return kwargs


# -- Policy tests ---------------------------------------------------------------


class TestSetPolicy:
    async def test_creates_and_emits_permission_granted(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        permission = await world.permission_service.set_policy(
            user, org_id, company_id, **set_policy_kwargs()
        )
        assert permission.decision is PermissionDecision.ALLOW
        rules = await world.permissions.list_for_principal(company_id, "agent", "agt_01AGENT")
        assert len(rules) == 1
        event = world.bus.events[-1]
        assert event.type == "permission.granted"
        assert event.company_id == company_id
        assert event.payload["permission_id"] == str(permission.id)
        assert event.payload["decision"] == "allow"

    async def test_upsert_replaces_existing_rule(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        first = await world.permission_service.set_policy(
            user, org_id, company_id, **set_policy_kwargs()
        )
        second = await world.permission_service.set_policy(
            user,
            org_id,
            company_id,
            **set_policy_kwargs(decision=PermissionDecision.DENY, constraints={"max": 5}),
        )
        rules = await world.permissions.list_for_principal(company_id, "agent", "agt_01AGENT")
        assert len(rules) == 1
        assert rules[0].decision is PermissionDecision.DENY
        assert rules[0].constraints == {"max": 5}
        # The replaced rule keeps the original id (update, not insert).
        assert rules[0].id == first.id
        assert second.id == first.id
        assert world.bus.events[-1].type == "permission.revoked"
        granted = [e.type for e in world.bus.events]
        assert granted.count("permission.granted") == 1
        assert granted.count("permission.revoked") == 1

    async def test_requires_member(self) -> None:
        world = World()
        outsider = User.register(email="out@example.com", hashed_password="x", display_name="Out")
        organization_id = OrganizationId.generate()
        company_id = CompanyId.generate()
        with pytest.raises(NotAMember):
            await world.permission_service.set_policy(
                outsider, organization_id, company_id, **set_policy_kwargs()
            )


# -- Engine tests -----------------------------------------------------------------


class TestCheckEngine:
    async def test_default_deny_when_no_rules(self) -> None:
        world = World()
        _, _, company_id = await make_member(world)
        decision = await world.permission_service.check(
            "agent", "agt_01AGENT", company_id, "tool", "web.search@1.0.0", "web.search"
        )
        assert decision is PermissionDecision.DENY

    async def test_default_decision_override(self) -> None:
        world = World()
        _, _, company_id = await make_member(world)
        decision = await world.permission_service.check(
            "agent",
            "agt_01AGENT",
            company_id,
            "tool",
            "web.search@1.0.0",
            "web.search",
            default_decision=PermissionDecision.ALLOW,
        )
        assert decision is PermissionDecision.ALLOW

    async def test_agent_rule_beats_company_default(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        svc = world.permission_service
        # Company-wide default (principal_id None): deny.
        await svc.set_policy(
            user,
            org_id,
            company_id,
            **set_policy_kwargs(principal_id=None, decision=PermissionDecision.DENY),
        )
        # Agent-specific rule: allow.
        await svc.set_policy(user, org_id, company_id, **set_policy_kwargs())
        decision = await svc.check(
            "agent", "agt_01AGENT", company_id, "tool", "web.search@1.0.0", "web.search"
        )
        assert decision is PermissionDecision.ALLOW

    async def test_company_default_applies_when_no_agent_rule(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        svc = world.permission_service
        await svc.set_policy(
            user,
            org_id,
            company_id,
            **set_policy_kwargs(principal_id=None, decision=PermissionDecision.ALLOW),
        )
        decision = await svc.check(
            "agent", "agt_01OTHER", company_id, "tool", "web.search@1.0.0", "web.search"
        )
        assert decision is PermissionDecision.ALLOW

    async def test_exact_resource_beats_wildcard(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        svc = world.permission_service
        # Wildcard deny for all tools; exact allow for web.search@1.0.0.
        await svc.set_policy(
            user,
            org_id,
            company_id,
            **set_policy_kwargs(resource_id=None, decision=PermissionDecision.DENY),
        )
        await svc.set_policy(user, org_id, company_id, **set_policy_kwargs())
        assert (
            await svc.check(
                "agent", "agt_01AGENT", company_id, "tool", "web.search@1.0.0", "web.search"
            )
            is PermissionDecision.ALLOW
        )
        # Other resources fall back to the wildcard deny.
        assert (
            await svc.check(
                "agent", "agt_01AGENT", company_id, "tool", "email.send@1.0.0", "email.send"
            )
            is PermissionDecision.DENY
        )

    async def test_resource_type_mismatch_does_not_match(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        await world.permission_service.set_policy(user, org_id, company_id, **set_policy_kwargs())
        decision = await world.permission_service.check(
            "agent", "agt_01AGENT", company_id, "model", "web.search@1.0.0", "web.search"
        )
        assert decision is PermissionDecision.DENY

    async def test_require_approval_for_gate(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        svc = world.permission_service
        await svc.set_policy(user, org_id, company_id, **set_policy_kwargs())
        await svc.require_approval_for(
            "agent", "agt_01AGENT", company_id, "tool", "web.search@1.0.0", "web.search"
        )
        with pytest.raises(PermissionDenied):
            await svc.require_approval_for(
                "agent", "agt_01AGENT", company_id, "tool", "email.send@1.0.0", "email.send"
            )
        await svc.set_policy(
            user,
            org_id,
            company_id,
            **set_policy_kwargs(
                resource_id="payments@1.0.0",
                action="payments.transfer",
                decision=PermissionDecision.APPROVAL_REQUIRED,
            ),
        )
        with pytest.raises(ApprovalRequired):
            await svc.require_approval_for(
                "agent", "agt_01AGENT", company_id, "tool", "payments@1.0.0", "payments.transfer"
            )


# -- Approval lifecycle tests ----------------------------------------------------


class TestApprovalLifecycle:
    async def test_request_and_decide_with_events(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        approval = await world.approval_service.request_approval(
            user,
            org_id,
            company_id,
            requested_by=user.id,
            action="payments.transfer",
            resource_type="tool",
            resource_id="payments@1.0.0",
            reason="large amount",
        )
        assert approval.status is ApprovalStatus.PENDING
        requested = world.bus.events[-1]
        assert requested.type == "approval.requested"
        assert requested.payload["approval_id"] == str(approval.id)

        decided = await world.approval_service.decide_approval(
            user, org_id, company_id, approval.id, decision="approved"
        )
        assert decided.status is ApprovalStatus.APPROVED
        assert decided.decided_by == user.id
        event = world.bus.events[-1]
        assert event.type == "approval.decided"
        assert event.payload["decision"] == "approved"
        assert event.payload["decided_by"] == str(user.id)

    async def test_decide_twice_is_invalid(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        approval = await world.approval_service.request_approval(
            user,
            org_id,
            company_id,
            requested_by=user.id,
            action="x",
            resource_type="tool",
        )
        await world.approval_service.decide_approval(
            user, org_id, company_id, approval.id, decision="approved"
        )
        with pytest.raises(InvalidStatusTransition):
            await world.approval_service.decide_approval(
                user, org_id, company_id, approval.id, decision="rejected"
            )

    async def test_reject_path(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        approval = await world.approval_service.request_approval(
            user,
            org_id,
            company_id,
            requested_by=user.id,
            action="x",
            resource_type="tool",
        )
        decided = await world.approval_service.decide_approval(
            user, org_id, company_id, approval.id, decision="rejected"
        )
        assert decided.status is ApprovalStatus.REJECTED

    async def test_decide_expired_approval_raises(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        approval = await world.approval_service.request_approval(
            user,
            org_id,
            company_id,
            requested_by=user.id,
            action="x",
            resource_type="tool",
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1),
        )
        with pytest.raises(ValidationError):
            await world.approval_service.decide_approval(
                user, org_id, company_id, approval.id, decision="approved"
            )

    async def test_list_approvals_pending_only(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        first = await world.approval_service.request_approval(
            user, org_id, company_id, requested_by=user.id, action="a", resource_type="tool"
        )
        await world.approval_service.request_approval(
            user, org_id, company_id, requested_by=user.id, action="b", resource_type="tool"
        )
        await world.approval_service.decide_approval(
            user, org_id, company_id, first.id, decision="approved"
        )
        pending = await world.approval_service.list_approvals(
            user, org_id, company_id, pending_only=True
        )
        assert [a.action for a in pending] == ["b"]
        everything = await world.approval_service.list_approvals(user, org_id, company_id)
        assert len(everything) == 2

    async def test_cross_tenant_approval_isolated(self) -> None:
        world = World()
        user, org_id, company_id = await make_member(world)
        approval = await world.approval_service.request_approval(
            user, org_id, company_id, requested_by=user.id, action="a", resource_type="tool"
        )
        outsider = User.register(email="out@example.com", hashed_password="x", display_name="Out")
        other_org = OrganizationId.generate()
        with pytest.raises(NotAMember):
            await world.approval_service.decide_approval(
                outsider, other_org, company_id, approval.id, decision="approved"
            )
