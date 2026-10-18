"""Phase 3 Slice 1 — execution permission gate security tests.

The D-1 invariant (mandate §10 / Phase 2 review): NO execution may occur when
the required permission is absent, denied, or requires un-granted approval.
These tests exercise the real stack (SQLite + real PermissionService):
- deny-by-default: no policy -> PermissionDenied, no execution record
- explicit deny -> PermissionDenied, no execution record
- approval_required -> pending Approval created, no execution record
- after approval granted + policy upgraded -> execution proceeds
- the gate runs before the runtime: a runtime that would "succeed" is never
  invoked when the policy denies
"""

from __future__ import annotations

from typing import Any

import pytest
from polsia.adapters.events import InProcessEventBus
from polsia.adapters.persistence.permission_repositories import (
    SqlAlchemyApprovalRepository,
    SqlAlchemyPermissionRepository,
)
from polsia.adapters.persistence.registry_repositories import SqlAlchemyAgentRepository
from polsia.adapters.persistence.repositories import (
    SqlAlchemyCompanyRepository,
    SqlAlchemyKpiRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyUserRepository,
)
from polsia.adapters.persistence.task_repositories import (
    SqlAlchemyTaskDependencyRepository,
    SqlAlchemyTaskExecutionRepository,
    SqlAlchemyTaskRepository,
)
from polsia.adapters.runtime import MockRuntime
from polsia.adapters.security.passwords import BcryptPasswordHasher
from polsia.core.auth.service import AuthService
from polsia.core.permissions.errors import ApprovalRequired, PermissionDenied
from polsia.core.permissions.values import PermissionDecision
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.ids import ApprovalId
from polsia.core.tenancy.entities import Company, Organization, OrgMembership, User
from polsia.core.tenancy.values import MembershipRole
from polsia.infra.db import create_all_metadata
from polsia.services.execution_authorization import ExecutionAuthorizationService
from polsia.services.orchestration import OrchestrationService
from polsia.services.permissions import ApprovalService, PermissionService
from polsia.services.registries import AgentService
from polsia.services.tasks import ExecutionService, TaskService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


class _NullTokens:
    def issue_access_token(self, user_id: object) -> str:  # noqa: ARG002
        return ""

    def issue_refresh_token(self, user_id: object) -> str:  # noqa: ARG002
        return ""

    def verify_token(self, token: str) -> object:  # noqa: ARG002
        raise ValueError("no tokens in this test env")


@pytest.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            bus = InProcessEventBus()
            users = SqlAlchemyUserRepository(session)
            user: User = await AuthService(users, BcryptPasswordHasher(), _NullTokens()).register(
                "gate@example.com", "password-123", "Gate"
            )
            companies = SqlAlchemyCompanyRepository(session)
            memberships = SqlAlchemyMembershipRepository(session)
            from polsia.adapters.persistence.repositories import SqlAlchemyOrganizationRepository

            org = Organization.create(name="O", slug="gate-org", owner_user_id=user.id)
            await SqlAlchemyOrganizationRepository(session).add(org)
            await memberships.add(
                OrgMembership.create(
                    organization_id=org.id, user_id=user.id, role=MembershipRole.OWNER
                )
            )
            company = Company.create(organization_id=org.id, name="C", slug="gate-co")
            await companies.add(company)

            permission_repo = SqlAlchemyPermissionRepository(session)
            approval_repo = SqlAlchemyApprovalRepository(session)
            permissions = PermissionService(
                permission_repo, approval_repo, companies, memberships, bus
            )
            approvals = ApprovalService(approval_repo, companies, memberships, bus)
            agents_repo = SqlAlchemyAgentRepository(session)
            tasks = SqlAlchemyTaskRepository(session)
            deps = SqlAlchemyTaskDependencyRepository(session)
            executions = SqlAlchemyTaskExecutionRepository(session)

            agent_service = AgentService(agents_repo, companies, memberships, bus)
            agent = await agent_service.register_agent(user, org.id, company.id, name="gated")

            task_service = TaskService(tasks, deps, companies, memberships, bus)
            task = await task_service.create_task(user, org.id, company.id, title="gated work")

            # A runtime that would "succeed" if it were ever reached.
            runtime = MockRuntime()

            execution_service = ExecutionService(
                tasks,
                executions,
                deps,
                companies,
                memberships,
                bus,
                authorization=ExecutionAuthorizationService(
                    tasks=task_service,
                    agents=agents_repo,
                    companies=companies,
                    memberships=memberships,
                    permissions=permissions,
                    approvals=approvals,
                ),
            )
            orchestration = OrchestrationService(
                tasks=task_service,
                executions=execution_service,
                orchestrator=__import__(
                    "polsia.adapters.runtime", fromlist=["DeterministicOrchestrator"]
                ).DeterministicOrchestrator(),
                runtime=runtime,
                agents=agents_repo,
                kpis=SqlAlchemyKpiRepository(session),
                companies=companies,
                memberships=memberships,
                bus=bus,
                permissions=permissions,
                approvals=approvals,
            )

            async def executions_for(task_id: object) -> list[object]:
                return await executions.list_for_task(company.id, task_id)  # type: ignore[arg-type]

            yield {
                "user": user,
                "org": org,
                "company": company,
                "agent": agent,
                "task": task,
                "permissions": permissions,
                "approvals": approvals,
                "task_service": task_service,
                "execution_service": execution_service,
                "orchestration": orchestration,
                "executions_for": executions_for,
                "session": session,
            }
    finally:
        await engine.dispose()


def _allow(env: dict[str, Any], decision: PermissionDecision) -> None:
    """Synchronous policy helper (repo-level, no actor needed for setup)."""
    raise NotImplementedError


class TestExecutionGate:
    async def test_direct_attempt_service_denies_without_policy(self, env: dict[str, Any]) -> None:
        """ExecutionService itself cannot bypass the shared authorization boundary."""
        with pytest.raises(PermissionDenied):
            await env["execution_service"].start_execution(
                env["user"], env["org"].id, env["company"].id, env["task"].id
            )
        assert await env["executions_for"](env["task"].id) == []

    async def test_direct_attempt_service_persists_approval_without_attempt(
        self, env: dict[str, Any]
    ) -> None:
        user, org, company = env["user"], env["org"], env["company"]
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.APPROVAL_REQUIRED,
        )
        with pytest.raises(ApprovalRequired):
            await env["execution_service"].start_execution(user, org.id, company.id, env["task"].id)
        pending = await env["approvals"].list_approvals(user, org.id, company.id, pending_only=True)
        assert len(pending) == 1
        assert await env["executions_for"](env["task"].id) == []

    async def test_direct_attempt_service_allows_authorized_attempt(
        self, env: dict[str, Any]
    ) -> None:
        user, org, company = env["user"], env["org"], env["company"]
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        task, execution = await env["execution_service"].start_execution(
            user, org.id, company.id, env["task"].id
        )
        assert task.status.value == "running"
        assert execution.agent_id == env["agent"].id
        assert await env["executions_for"](env["task"].id) == [execution]

    async def test_no_policy_denies_by_default(self, env: dict[str, Any]) -> None:
        """No rule at all -> PermissionDenied, and NO execution record exists."""
        with pytest.raises(PermissionDenied):
            await env["orchestration"].execute_task(
                env["user"], env["org"].id, env["company"].id, env["task"].id
            )
        assert await env["executions_for"](env["task"].id) == []

    async def test_explicit_deny_blocks_execution(self, env: dict[str, Any]) -> None:
        user, org, company = env["user"], env["org"], env["company"]
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.DENY,
        )
        with pytest.raises(PermissionDenied):
            await env["orchestration"].execute_task(user, org.id, company.id, env["task"].id)
        assert await env["executions_for"](env["task"].id) == []

    async def test_task_specific_deny_overrides_company_allow(self, env: dict[str, Any]) -> None:
        """The gate evaluates the actual task ID, so a specific deny wins over
        the company-wide allow fallback."""
        user, org, company = env["user"], env["org"], env["company"]
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=str(env["task"].id),
            action="execute",
            decision=PermissionDecision.DENY,
        )
        with pytest.raises(PermissionDenied):
            await env["execution_service"].start_execution(user, org.id, company.id, env["task"].id)
        current = await env["task_service"].get_task(user, org.id, company.id, env["task"].id)
        assert current.status.value == "ready"
        assert current.attempts == 0
        assert await env["executions_for"](env["task"].id) == []

    async def test_task_specific_approval_overrides_company_allow(
        self, env: dict[str, Any]
    ) -> None:
        user, org, company = env["user"], env["org"], env["company"]
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=str(env["task"].id),
            action="execute",
            decision=PermissionDecision.APPROVAL_REQUIRED,
        )
        with pytest.raises(ApprovalRequired):
            await env["execution_service"].start_execution(user, org.id, company.id, env["task"].id)
        pending = await env["approvals"].list_approvals(user, org.id, company.id, pending_only=True)
        assert len(pending) == 1
        assert pending[0].resource_id == str(env["task"].id)
        current = await env["task_service"].get_task(user, org.id, company.id, env["task"].id)
        assert current.status.value == "ready"
        assert current.attempts == 0
        assert await env["executions_for"](env["task"].id) == []

    async def test_allow_allows_execution(self, env: dict[str, Any]) -> None:
        user, org, company = env["user"], env["org"], env["company"]
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        task, execution, result = await env["orchestration"].execute_task(
            user, org.id, company.id, env["task"].id
        )
        assert result.is_success()
        assert task.status.value == "completed"
        history = await env["executions_for"](env["task"].id)
        assert len(history) == 1
        assert history[0].id == execution.id

    async def test_approval_required_creates_pending_approval_and_no_execution(
        self, env: dict[str, Any]
    ) -> None:
        user, org, company = env["user"], env["org"], env["company"]
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.APPROVAL_REQUIRED,
        )
        with pytest.raises(ApprovalRequired) as excinfo:
            await env["orchestration"].execute_task(user, org.id, company.id, env["task"].id)
        assert excinfo.value.approval_id is not None
        assert isinstance(excinfo.value.approval_id, str)
        ApprovalId.parse(excinfo.value.approval_id)  # well-formed id
        pending = await env["approvals"].list_approvals(user, org.id, company.id, pending_only=True)
        assert len(pending) == 1
        assert pending[0].action == "execute"
        assert pending[0].resource_id == str(env["task"].id)
        # NO execution record; task status untouched (ready, not blocked)
        assert await env["executions_for"](env["task"].id) == []
        refreshed = await env["task_service"].get_task(user, org.id, company.id, env["task"].id)
        assert refreshed.status.value in {"pending", "ready"}

    async def test_declared_task_approval_is_required_even_with_policy_allow(
        self, env: dict[str, Any]
    ) -> None:
        user, org, company = env["user"], env["org"], env["company"]
        task = await env["task_service"].create_task(
            user,
            org.id,
            company.id,
            title="human-gated plan task",
            metadata={"requires_approval": True},
        )
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        with pytest.raises(ApprovalRequired):
            await env["execution_service"].start_execution(user, org.id, company.id, task.id)
        pending = await env["approvals"].list_approvals(user, org.id, company.id, pending_only=True)
        assert len(pending) == 1
        await env["approvals"].decide_approval(
            user, org.id, company.id, pending[0].id, decision="approved"
        )
        running, execution = await env["execution_service"].start_execution(
            user, org.id, company.id, task.id
        )
        assert running.status.value == "running"
        assert execution.agent_id == env["agent"].id

    async def test_approval_alone_is_not_enough_without_policy_upgrade(
        self, env: dict[str, Any]
    ) -> None:
        """Granting an approval does not bypass the policy: the engine still
        requires an ALLOW decision before execution."""
        user, org, company = env["user"], env["org"], env["company"]
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.APPROVAL_REQUIRED,
        )
        with pytest.raises(ApprovalRequired):
            await env["orchestration"].execute_task(user, org.id, company.id, env["task"].id)
        # approve the pending approval...
        pending = await env["approvals"].list_approvals(user, org.id, company.id, pending_only=True)
        await env["approvals"].decide_approval(
            user, org.id, company.id, pending[0].id, decision="approved"
        )
        # ...but the policy still says approval_required -> still gated
        with pytest.raises(ApprovalRequired):
            await env["orchestration"].execute_task(user, org.id, company.id, env["task"].id)
        assert await env["executions_for"](env["task"].id) == []

    async def test_gate_runs_before_runtime_is_invoked(self, env: dict[str, Any]) -> None:
        """Adversarial: a runtime that would claim success is NEVER reached
        when the policy denies — the gate precedes any runtime interaction."""
        user, org, company = env["user"], env["org"], env["company"]
        calls: list[object] = []

        class SpyRuntime(MockRuntime):
            async def execute(self, request: object) -> object:  # type: ignore[override]
                calls.append(request)
                return await super().execute(request)  # type: ignore[arg-type]

        orchestration = OrchestrationService(
            tasks=env["task_service"],
            executions=env["orchestration"]._executions,
            orchestrator=__import__(
                "polsia.adapters.runtime", fromlist=["DeterministicOrchestrator"]
            ).DeterministicOrchestrator(),
            runtime=SpyRuntime(),
            agents=env["orchestration"]._agents,
            kpis=env["orchestration"]._kpis,
            companies=env["orchestration"]._scope._companies,
            memberships=env["orchestration"]._scope._memberships,
            bus=InProcessEventBus(),
            permissions=env["permissions"],
        )
        with pytest.raises(PermissionDenied):
            await orchestration.execute_task(user, org.id, company.id, env["task"].id)
        assert calls == []  # runtime never invoked

    async def test_gate_is_per_company(self, env: dict[str, Any]) -> None:
        """An ALLOW in one company does not authorize execution in another."""
        user, org = env["user"], env["org"]
        await env["permissions"].set_policy(
            user,
            org.id,
            env["company"].id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        # a second company in the same org, with its own agent
        companies = env["orchestration"]._scope._companies
        company2 = Company.create(organization_id=org.id, name="C2", slug="gate-co-two")
        await companies.add(company2)
        agent2 = await AgentService(
            env["orchestration"]._agents,
            companies,
            env["orchestration"]._scope._memberships,
            InProcessEventBus(),
        ).register_agent(user, org.id, company2.id, name="gated-two")
        task2 = await env["task_service"].create_task(user, org.id, company2.id, title="other co")
        # no policy in company2 -> deny-by-default (per-company isolation)
        with pytest.raises(PermissionDenied):
            await env["orchestration"].execute_task(user, org.id, company2.id, task2.id)
        _ = agent2

    async def test_verification_failure_fails_execution(self, env: dict[str, Any]) -> None:
        user, org, company = env["user"], env["org"], env["company"]
        task = await env["task_service"].create_task(
            user,
            org.id,
            company.id,
            title="must return structured field",
            metadata={"verification": {"required_output_fields": ["receipt_id"]}},
        )
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        updated_task, execution, result = await env["orchestration"].execute_task(
            user, org.id, company.id, task.id
        )
        assert updated_task.status.value == "failed"
        assert execution.status.value == "failed"
        assert result.status.value == "failed"
        assert "receipt_id" in (result.error or "")

    async def test_declared_task_capability_must_be_supported(self, env: dict[str, Any]) -> None:
        user, org, company = env["user"], env["org"], env["company"]
        task = await env["task_service"].create_task(
            user,
            org.id,
            company.id,
            title="requires capability",
            assigned_skill="specialized-capability@1.0.0",
            assigned_agent_id=env["agent"].id,
        )
        await env["permissions"].set_policy(
            user,
            org.id,
            company.id,
            principal_type="agent",
            principal_id=str(env["agent"].id),
            resource_type="task",
            resource_id=None,
            action="execute",
            decision=PermissionDecision.ALLOW,
        )
        with pytest.raises(ValidationError, match="does not declare required capability"):
            await env["orchestration"].execute_task(user, org.id, company.id, task.id)
        assert await env["executions_for"](task.id) == []
