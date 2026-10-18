"""Shared authorization boundary for creating task execution attempts."""

from __future__ import annotations

from polsia.core.permissions.errors import ApprovalRequired, PermissionDenied
from polsia.core.permissions.values import PermissionDecision
from polsia.core.registry.entities import Agent
from polsia.core.registry.ports import AgentRepository
from polsia.core.shared.errors import NotFoundError, ValidationError
from polsia.core.shared.ids import AgentId, CompanyId, OrganizationId, TaskId
from polsia.core.tasks.entities import Task
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.permissions import ApprovalService, PermissionService
from polsia.services.planning import _CompanyScope
from polsia.services.tasks import TaskService


class ExecutionAuthorizationService:
    """Select an agent and require explicit permission before attempt creation."""

    def __init__(
        self,
        *,
        tasks: TaskService,
        agents: AgentRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        permissions: PermissionService,
        approvals: ApprovalService | None = None,
    ) -> None:
        self._tasks = tasks
        self._agents = agents
        self._scope = _CompanyScope(companies, memberships)
        self._permissions = permissions
        self._approvals = approvals

    async def select_agent(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task: Task,
        *,
        agent_id: AgentId | None = None,
    ) -> Agent:
        await self._scope.require_company(actor, organization_id, company_id)
        candidate_id = agent_id or task.assigned_agent_id
        if candidate_id is not None:
            agent = await self._agents.get(company_id, candidate_id)
            if agent is None:
                raise NotFoundError(f"agent {candidate_id} not found")
            if not agent.is_selectable():
                raise ValidationError(f"agent {candidate_id} is not selectable (disabled)")
            if task.assigned_skill and task.assigned_skill not in agent.capabilities:
                raise ValidationError(
                    f"agent {candidate_id} does not declare required capability "
                    f"{task.assigned_skill!r}"
                )
            return agent
        candidates = [
            agent
            for agent in await self._agents.list_for_company(company_id)
            if agent.is_selectable()
        ]
        if task.assigned_skill:
            candidates = [
                agent for agent in candidates if task.assigned_skill in agent.capabilities
            ]
        if candidates:
            return min(candidates, key=lambda candidate: str(candidate.id))
        if task.assigned_skill:
            raise ValidationError(
                f"no selectable agent declares required capability {task.assigned_skill!r}"
            )
        raise ValidationError("no selectable agent is registered for this company")

    async def authorize_task(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        *,
        agent_id: AgentId | None = None,
    ) -> tuple[Task, Agent]:
        """Return the exact task snapshot and agent covered by the decision."""
        await self._scope.require_company(actor, organization_id, company_id)
        task = await self._tasks.get_task(actor, organization_id, company_id, task_id)
        agent = await self.select_agent(actor, organization_id, company_id, task, agent_id=agent_id)
        await self._require_allowed(actor, organization_id, company_id, task, agent)
        return task, agent

    async def authorize(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        *,
        agent_id: AgentId | None = None,
    ) -> Agent:
        _, agent = await self.authorize_task(
            actor, organization_id, company_id, task_id, agent_id=agent_id
        )
        return agent

    async def require_allowed(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task: Task,
        agent: Agent,
    ) -> None:
        await self._require_allowed(actor, organization_id, company_id, task, agent)

    async def _require_allowed(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task: Task,
        agent: Agent,
    ) -> None:
        decision = await self._permissions.check(
            principal_type="agent",
            principal_id=str(agent.id),
            company_id=company_id,
            resource_type="task",
            resource_id=str(task.id),
            action="execute",
        )
        if decision is PermissionDecision.ALLOW:
            if task.metadata.get("requires_approval") is True:
                if self._approvals is None:
                    raise PermissionDenied(
                        f"task {task.id} requires approval, but no approval service is configured"
                    )
                if await self._approvals.has_approved(
                    actor,
                    organization_id,
                    company_id,
                    action="execute",
                    resource_type="task",
                    resource_id=str(task.id),
                ):
                    return
                approval = await self._approvals.request_approval(
                    actor,
                    organization_id,
                    company_id,
                    requested_by=actor.id,
                    action="execute",
                    resource_type="task",
                    resource_id=str(task.id),
                    reason=f"task {task.id} declares execution approval required",
                )
                raise ApprovalRequired(
                    f"task {task.id} requires human approval before execution",
                    approval_id=str(approval.id),
                )
            return
        if decision is PermissionDecision.DENY:
            raise PermissionDenied(
                f"agent {agent.id} is not allowed to execute tasks in company "
                f"{company_id} (policy decision: deny)"
            )
        if self._approvals is None:
            raise PermissionDenied(
                f"agent {agent.id} requires approval to execute tasks in "
                f"company {company_id}, but no approval service is configured"
            )
        approval = await self._approvals.request_approval(
            actor,
            organization_id,
            company_id,
            requested_by=actor.id,
            action="execute",
            resource_type="task",
            resource_id=str(task.id),
            reason=f"agent {agent.id} execution of task {task.id} requires approval",
        )
        raise ApprovalRequired(
            f"agent {agent.id} execution of task {task.id} requires approval",
            approval_id=str(approval.id),
        )
