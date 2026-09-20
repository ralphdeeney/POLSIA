"""Phase 2 security suite: the full tenant-isolation matrix (spec §23).

Consolidated defense-in-depth checks through the real stack (SQLite):
- User A cannot access Company B (any Phase 2 primitive).
- Org A cannot access Org B.
- A task cannot be executed under another company's context.
- Memory search cannot leak another tenant's data.
- Usage records are tenant-isolated.
- Agents cannot invoke unauthorized tools (deny-by-default engine).
- Agents cannot bypass approval (approval_required gates until decided).
- Global capability (registry) separation: registration != authorization.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from polsia.adapters.cost import NullCostCalculator
from polsia.adapters.events import InProcessEventBus
from polsia.adapters.persistence.memory_repositories import SqlAlchemyMemoryStore
from polsia.adapters.persistence.registry_repositories import SqlAlchemyAgentRepository
from polsia.adapters.persistence.repositories import (
    SqlAlchemyCompanyRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from polsia.adapters.persistence.task_repositories import (
    SqlAlchemyTaskDependencyRepository,
    SqlAlchemyTaskExecutionRepository,
    SqlAlchemyTaskRepository,
)
from polsia.adapters.persistence.usage_repositories import SqlAlchemyUsageRepository
from polsia.api.main import create_app
from polsia.core.cost.ports import UsageRecord
from polsia.core.permissions.errors import ApprovalRequired
from polsia.core.permissions.values import PermissionDecision
from polsia.core.shared.ids import CompanyId, TaskExecutionId, TaskId
from polsia.core.tenancy.entities import User
from polsia.infra.db import create_all_metadata
from polsia.infra.settings import Settings
from polsia.services.memory import MemoryService
from polsia.services.permissions import PermissionService
from polsia.services.registries import AgentService
from polsia.services.tasks import ExecutionService, TaskService
from polsia.services.usage import UsageService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

PASSWORD = "super-secret-1"


@pytest.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        app = create_app(Settings(env="test", jwt_secret="test-secret"), session_factory=factory)
        transport = ASGITransport(app=app)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        await engine.dispose()


async def register(client: AsyncClient, email: str) -> dict[str, Any]:
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(user: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['tokens']['access_token']}"}


class TestPhase2IsolationMatrix:
    async def test_user_a_cannot_access_company_b(self, client: AsyncClient) -> None:
        """Every company-scoped primitive denies a non-member."""
        user_a = await register(client, "iso-a@example.com")
        user_b = await register(client, "iso-b@example.com")
        headers_a, headers_b = bearer(user_a), bearer(user_b)
        response = await client.post(
            "/organizations",
            json={"name": "Org A", "slug": "iso-a"},
            headers=headers_a,
        )
        org_a = response.json()
        response = await client.post(
            f"/organizations/{org_a['id']}/companies",
            json={"name": "Company A", "slug": "company-a"},
            headers=headers_a,
        )
        company_a = response.json()
        base = f"/organizations/{org_a['id']}/companies/{company_a['id']}"

        # B (not a member of org A) is denied on every primitive
        for path, method, payload in [
            ("/goals", "post", {"title": "hijack"}),
            ("/kpis", "post", {"name": "h", "direction": "increase", "target_value": 1}),
            ("/tasks", "post", {"title": "hijack"}),
            ("/agents", "post", {"name": "hijack"}),
            ("/memory", "post", {"scope": "company", "type": "t", "content": "x"}),
            (
                "/approvals",
                "post",
                {"requested_by": "usr_b", "action": "email.send", "resource_type": "tool"},
            ),
            ("/goals", "get", None),
            ("/tasks", "get", None),
            ("/memory", "get", None),
            ("/usage", "get", None),
            ("/permissions", "get", None),
        ]:
            if method == "post":
                response = await client.post(base + path, json=payload, headers=headers_b)
            else:
                response = await client.get(base + path, headers=headers_b)
            assert response.status_code == 403, f"{method} {path} -> {response.status_code}"

    async def test_org_a_cannot_access_org_b(self, client: AsyncClient) -> None:
        """Org-scoped endpoints (companies listing) are per-org."""
        user_a = await register(client, "iso-c@example.com")
        headers_a = bearer(user_a)
        response = await client.post(
            "/organizations", json={"name": "Org A", "slug": "iso-c"}, headers=headers_a
        )
        org_a = response.json()
        # another user's org
        user_b = await register(client, "iso-d@example.com")
        response = await client.post(
            "/organizations", json={"name": "Org B", "slug": "iso-d"}, headers=bearer(user_b)
        )
        org_b = response.json()
        # A's org does not contain B's companies (empty), and B's org id cannot
        # be used by A to create companies without membership... creating a
        # company in org B is denied for A:
        response = await client.post(
            f"/organizations/{org_b['id']}/companies",
            json={"name": "Hijack", "slug": "hijack-co"},
            headers=headers_a,
        )
        assert response.status_code == 403
        _ = org_a

    async def test_task_cannot_execute_under_another_companys_context(
        self, client: AsyncClient
    ) -> None:
        """Cross-company task ids are simply not found (scope-checked)."""
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await create_all_metadata(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            bus = InProcessEventBus()
            session = factory()
            users = SqlAlchemyUserRepository(session)
            from polsia.adapters.security.passwords import BcryptPasswordHasher
            from polsia.core.auth.service import AuthService

            class _NT:
                def hash(self, p: str) -> str:
                    return "x"

                def verify(self, a: str, b: str) -> bool:
                    return True

                def issue_access_token(self, u: object) -> str:
                    return ""

                def issue_refresh_token(self, u: object) -> str:
                    return ""

                def verify_token(self, t: str) -> object:
                    raise ValueError

            user: User = await AuthService(users, BcryptPasswordHasher(), _NT()).register(
                "iso-e@example.com", "password-123", "E"
            )
            from polsia.core.tenancy.entities import Company, Organization, OrgMembership
            from polsia.core.tenancy.values import MembershipRole

            memberships = SqlAlchemyMembershipRepository(session)
            companies = SqlAlchemyCompanyRepository(session)
            org1 = Organization.create(name="O1", slug="iso-e", owner_user_id=user.id)
            await SqlAlchemyOrganizationRepository(session).add(org1)
            await memberships.add(
                OrgMembership.create(
                    organization_id=org1.id, user_id=user.id, role=MembershipRole.OWNER
                )
            )
            company1 = Company.create(organization_id=org1.id, name="C1", slug="co-one")
            company2 = Company.create(organization_id=org1.id, name="C2", slug="co-two")
            await companies.add(company1)
            await companies.add(company2)
            tasks = SqlAlchemyTaskRepository(session)
            deps = SqlAlchemyTaskDependencyRepository(session)
            executions = SqlAlchemyTaskExecutionRepository(session)
            task_service = TaskService(tasks, deps, companies, memberships, bus)
            exec_service = ExecutionService(tasks, executions, deps, companies, memberships, bus)
            task = await task_service.create_task(user, org1.id, company1.id, title="private")
            # the same task id under company2 is NOT FOUND (scoped)
            from polsia.core.shared.errors import NotFoundError

            with pytest.raises(NotFoundError):
                await task_service.get_task(user, org1.id, company2.id, task.id)
            with pytest.raises(NotFoundError):
                await exec_service.start_execution(user, org1.id, company2.id, task.id)
            await session.close()
        finally:
            await engine.dispose()

    async def test_memory_search_cannot_leak_cross_tenant(self, client: AsyncClient) -> None:
        """The DB-backed keyword search is always company-scoped."""
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await create_all_metadata(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            bus = InProcessEventBus()
            session = factory()
            users = SqlAlchemyUserRepository(session)
            from polsia.adapters.security.passwords import BcryptPasswordHasher
            from polsia.core.auth.service import AuthService

            class _NT:
                def hash(self, p: str) -> str:
                    return "x"

                def verify(self, a: str, b: str) -> bool:
                    return True

                def issue_access_token(self, u: object) -> str:
                    return ""

                def issue_refresh_token(self, u: object) -> str:
                    return ""

                def verify_token(self, t: str) -> object:
                    raise ValueError

            user: User = await AuthService(users, BcryptPasswordHasher(), _NT()).register(
                "iso-f@example.com", "password-123", "F"
            )
            from polsia.core.tenancy.entities import Company, Organization, OrgMembership
            from polsia.core.tenancy.values import MembershipRole

            memberships = SqlAlchemyMembershipRepository(session)
            companies = SqlAlchemyCompanyRepository(session)
            org = Organization.create(name="O", slug="iso-f", owner_user_id=user.id)
            await SqlAlchemyOrganizationRepository(session).add(org)
            await memberships.add(
                OrgMembership.create(
                    organization_id=org.id, user_id=user.id, role=MembershipRole.OWNER
                )
            )
            company1 = Company.create(organization_id=org.id, name="C1", slug="co-one")
            company2 = Company.create(organization_id=org.id, name="C2", slug="co-two")
            await companies.add(company1)
            await companies.add(company2)
            memory = SqlAlchemyMemoryStore(session)
            memory_service = MemoryService(memory, companies, memberships, bus)
            await memory_service.write(
                user,
                org.id,
                company1.id,
                scope="company",
                type="note",
                content="shared keyword secret-of-c1",
            )
            await memory_service.write(
                user,
                org.id,
                company2.id,
                scope="company",
                type="note",
                content="shared keyword secret-of-c2",
            )
            # searching company1 never returns company2's records
            hits = await memory_service.search(
                user, org.id, company1.id, "shared keyword", scope=None
            )
            assert [h.content for h in hits] == ["shared keyword secret-of-c1"]
            await session.close()
        finally:
            await engine.dispose()

    async def test_usage_records_are_tenant_isolated(self, client: AsyncClient) -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await create_all_metadata(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            session = factory()
            usage = SqlAlchemyUsageRepository(session)
            await usage.add(_make_usage(company1_id := CompanyId.generate()))
            # company2 sees nothing
            records = await usage.list_for_company(CompanyId.generate())
            assert records == []
            assert await usage.list_for_company(company1_id)  # owner sees its own
            await session.close()
        finally:
            await engine.dispose()

    async def test_agent_cannot_invoke_unauthorized_tool(self, client: AsyncClient) -> None:
        """The engine denies by default: no rule -> deny; a deny rule -> deny."""
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await create_all_metadata(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            bus = InProcessEventBus()
            session = factory()
            users = SqlAlchemyUserRepository(session)
            from polsia.adapters.security.passwords import BcryptPasswordHasher
            from polsia.core.auth.service import AuthService

            class _NT:
                def hash(self, p: str) -> str:
                    return "x"

                def verify(self, a: str, b: str) -> bool:
                    return True

                def issue_access_token(self, u: object) -> str:
                    return ""

                def issue_refresh_token(self, u: object) -> str:
                    return ""

                def verify_token(self, t: str) -> object:
                    raise ValueError

            user: User = await AuthService(users, BcryptPasswordHasher(), _NT()).register(
                "iso-g@example.com", "password-123", "G"
            )
            from polsia.core.tenancy.entities import Company, Organization, OrgMembership
            from polsia.core.tenancy.values import MembershipRole

            memberships = SqlAlchemyMembershipRepository(session)
            companies = SqlAlchemyCompanyRepository(session)
            org = Organization.create(name="O", slug="iso-g", owner_user_id=user.id)
            await SqlAlchemyOrganizationRepository(session).add(org)
            await memberships.add(
                OrgMembership.create(
                    organization_id=org.id, user_id=user.id, role=MembershipRole.OWNER
                )
            )
            company = Company.create(organization_id=org.id, name="C", slug="c")
            await companies.add(company)

            from polsia.adapters.persistence.permission_repositories import (
                SqlAlchemyApprovalRepository,
                SqlAlchemyPermissionRepository,
            )

            permissions: PermissionRepository = SqlAlchemyPermissionRepository(session)
            approvals: ApprovalRepository = SqlAlchemyApprovalRepository(session)
            from polsia.core.permissions.ports import ApprovalRepository, PermissionRepository

            usage_repo = SqlAlchemyUsageRepository(session)
            service = PermissionService(permissions, approvals, companies, memberships, bus)
            usage_service = UsageService(
                usage_repo, companies, memberships, bus, NullCostCalculator()
            )
            _ = usage_service

            agent = await AgentService(
                SqlAlchemyAgentRepository(session), companies, memberships, bus
            ).register_agent(user, org.id, company.id, name="restricted")

            # deny rule for email.send; no rule for production.deploy
            await service.set_policy(
                user,
                org.id,
                company.id,
                principal_type="agent",
                principal_id=str(agent.id),
                resource_type="tool",
                resource_id="email@1.0.0",
                action="email.send",
                decision=PermissionDecision.DENY,
            )
            assert (
                await service.check(
                    principal_type="agent",
                    principal_id=str(agent.id),
                    company_id=company.id,
                    resource_type="tool",
                    resource_id="email@1.0.0",
                    action="email.send",
                )
                is PermissionDecision.DENY
            )
            # dangerous operation with NO rule -> default deny
            assert (
                await service.check(
                    principal_type="agent",
                    principal_id=str(agent.id),
                    company_id=company.id,
                    resource_type="tool",
                    resource_id="deploy@1.0.0",
                    action="production.deploy",
                )
                is PermissionDecision.DENY
            )
            # a permitted action
            await service.set_policy(
                user,
                org.id,
                company.id,
                principal_type="agent",
                principal_id=str(agent.id),
                resource_type="tool",
                resource_id="web@1.0.0",
                action="web.search",
                decision=PermissionDecision.ALLOW,
            )
            assert (
                await service.check(
                    principal_type="agent",
                    principal_id=str(agent.id),
                    company_id=company.id,
                    resource_type="tool",
                    resource_id="web@1.0.0",
                    action="web.search",
                )
                is PermissionDecision.ALLOW
            )
            await session.close()
        finally:
            await engine.dispose()

    async def test_agent_cannot_bypass_approval(self, client: AsyncClient) -> None:
        """approval_required gates the action until an approval is decided."""
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await create_all_metadata(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            bus = InProcessEventBus()
            session = factory()
            users = SqlAlchemyUserRepository(session)
            from polsia.adapters.security.passwords import BcryptPasswordHasher
            from polsia.core.auth.service import AuthService

            class _NT:
                def hash(self, p: str) -> str:
                    return "x"

                def verify(self, a: str, b: str) -> bool:
                    return True

                def issue_access_token(self, u: object) -> str:
                    return ""

                def issue_refresh_token(self, u: object) -> str:
                    return ""

                def verify_token(self, t: str) -> object:
                    raise ValueError

            user: User = await AuthService(users, BcryptPasswordHasher(), _NT()).register(
                "iso-h@example.com", "password-123", "H"
            )
            from polsia.core.tenancy.entities import Company, Organization, OrgMembership
            from polsia.core.tenancy.values import MembershipRole

            memberships = SqlAlchemyMembershipRepository(session)
            companies = SqlAlchemyCompanyRepository(session)
            org = Organization.create(name="O", slug="iso-h", owner_user_id=user.id)
            await SqlAlchemyOrganizationRepository(session).add(org)
            await memberships.add(
                OrgMembership.create(
                    organization_id=org.id, user_id=user.id, role=MembershipRole.OWNER
                )
            )
            company = Company.create(organization_id=org.id, name="C", slug="c")
            await companies.add(company)

            from polsia.adapters.persistence.permission_repositories import (
                SqlAlchemyApprovalRepository,
                SqlAlchemyPermissionRepository,
            )

            service = PermissionService(
                SqlAlchemyPermissionRepository(session),
                SqlAlchemyApprovalRepository(session),
                companies,
                memberships,
                bus,
            )
            # a policy requiring human approval for the dangerous op
            await service.set_policy(
                user,
                org.id,
                company.id,
                principal_type="agent",
                principal_id="agt_any",
                resource_type="tool",
                resource_id="spend@1.0.0",
                action="spend.money",
                decision=PermissionDecision.APPROVAL_REQUIRED,
            )
            # the gate blocks (ApprovalRequired) while the policy requires approval
            with pytest.raises(ApprovalRequired):
                await service.require_approval_for(
                    "agent",
                    "agt_any",
                    company.id,
                    "tool",
                    "spend@1.0.0",
                    "spend.money",
                )
            from polsia.services.permissions import ApprovalService

            approval_service = ApprovalService(
                SqlAlchemyApprovalRepository(session), companies, memberships, bus
            )
            approval = await approval_service.request_approval(
                user,
                org.id,
                company.id,
                requested_by=user.id,
                action="spend.money",
                resource_type="tool",
                resource_id="spend@1.0.0",
                reason="campaign budget",
            )
            await approval_service.decide_approval(
                user, org.id, company.id, approval.id, decision="approved"
            )
            # the human decision upgrades the policy; the gate passes now
            await service.set_policy(
                user,
                org.id,
                company.id,
                principal_type="agent",
                principal_id="agt_any",
                resource_type="tool",
                resource_id="spend@1.0.0",
                action="spend.money",
                decision=PermissionDecision.ALLOW,
            )
            await service.require_approval_for(
                "agent",
                "agt_any",
                company.id,
                "tool",
                "spend@1.0.0",
                "spend.money",
            )
            await session.close()
        finally:
            await engine.dispose()

    async def test_global_capability_registration_is_not_authorization(
        self, client: AsyncClient
    ) -> None:
        """A registered (global) tool does not imply any agent may use it:
        with no permission rules, the engine denies the same tool."""
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await create_all_metadata(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            bus = InProcessEventBus()
            session = factory()
            from polsia.adapters.persistence.permission_repositories import (
                SqlAlchemyApprovalRepository,
                SqlAlchemyPermissionRepository,
            )
            from polsia.adapters.persistence.registry_repositories import (
                SqlAlchemyToolRepository,
            )
            from polsia.services.registries import ToolService

            companies = SqlAlchemyCompanyRepository(session)
            memberships = SqlAlchemyMembershipRepository(session)
            users = SqlAlchemyUserRepository(session)
            from polsia.adapters.security.passwords import BcryptPasswordHasher
            from polsia.core.auth.service import AuthService

            class _NT:
                def hash(self, p: str) -> str:
                    return "x"

                def verify(self, a: str, b: str) -> bool:
                    return True

                def issue_access_token(self, u: object) -> str:
                    return ""

                def issue_refresh_token(self, u: object) -> str:
                    return ""

                def verify_token(self, t: str) -> object:
                    raise ValueError

            user: User = await AuthService(users, BcryptPasswordHasher(), _NT()).register(
                "iso-i@example.com", "password-123", "I"
            )
            from polsia.core.tenancy.entities import Company, Organization, OrgMembership
            from polsia.core.tenancy.values import MembershipRole

            org = Organization.create(name="O", slug="iso-i", owner_user_id=user.id)
            await SqlAlchemyOrganizationRepository(session).add(org)
            await memberships.add(
                OrgMembership.create(
                    organization_id=org.id, user_id=user.id, role=MembershipRole.OWNER
                )
            )
            company = Company.create(organization_id=org.id, name="C", slug="c")
            await companies.add(company)

            await ToolService(SqlAlchemyToolRepository(session), bus).register_tool(
                name="web_search", version="1.0.0"
            )
            service = PermissionService(
                SqlAlchemyPermissionRepository(session),
                SqlAlchemyApprovalRepository(session),
                companies,
                memberships,
                bus,
            )
            # globally registered, but NO permission rule -> deny
            assert (
                await service.check(
                    principal_type="agent",
                    principal_id="agt_x",
                    company_id=company.id,
                    resource_type="tool",
                    resource_id="web_search@1.0.0",
                    action="web.search",
                )
                is PermissionDecision.DENY
            )
            await session.close()
        finally:
            await engine.dispose()


def _make_usage(company_id: CompanyId) -> UsageRecord:
    return UsageRecord.create(
        company_id=company_id,
        execution_id=TaskExecutionId.generate(),
        task_id=TaskId.generate(),
        prompt_tokens=10,
        completion_tokens=5,
    )


_ = Decimal  # referenced for typing parity
