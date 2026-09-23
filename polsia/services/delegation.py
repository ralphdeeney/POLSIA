"""Company-scoped task delegation to registered agents."""

from __future__ import annotations

import dataclasses

from polsia.core.registry.ports import AgentRepository
from polsia.core.shared.errors import NotFoundError, ValidationError
from polsia.core.shared.events import EventBus, EventEnvelope, EventStore, emit
from polsia.core.shared.ids import AgentId, CompanyId, OrganizationId, TaskId
from polsia.core.tasks.values import TaskStatus
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.planning import _CompanyScope
from polsia.services.tasks import TaskService


@dataclasses.dataclass(frozen=True)
class Delegation:
    """Recorded assignment decision for a task."""

    company_id: CompanyId
    task_id: TaskId
    agent_id: AgentId
    rationale: str


class DelegationService:
    """Select and assign a selectable company agent without executing it."""

    def __init__(
        self,
        tasks: TaskService,
        agents: AgentRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        store: EventStore | None = None,
    ) -> None:
        self._tasks = tasks
        self._agents = agents
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus
        self._store = store

    async def delegate(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        *,
        agent_id: AgentId | None = None,
    ) -> Delegation:
        """Assign a task explicitly or deterministically by declared capability.

        When a task names an assigned skill, only agents declaring that exact
        capability are eligible. No model-generated assignment is trusted.
        """
        await self._scope.require_company(actor, organization_id, company_id)
        task = await self._tasks.get_task(actor, organization_id, company_id, task_id)
        if task.status not in {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.RETRYING}:
            raise ValidationError(f"cannot delegate task in {task.status.value!r} status")

        if agent_id is not None:
            agent = await self._agents.get(company_id, agent_id)
            if agent is None:
                raise NotFoundError(f"agent {agent_id} not found")
            if not agent.is_selectable():
                raise ValidationError(f"agent {agent_id} is not selectable")
            if task.assigned_skill and task.assigned_skill not in agent.capabilities:
                raise ValidationError(
                    f"agent {agent_id} does not declare required capability {task.assigned_skill!r}"
                )
            rationale = "explicitly selected selectable agent"
        else:
            candidates = [
                a for a in await self._agents.list_for_company(company_id) if a.is_selectable()
            ]
            if task.assigned_skill:
                candidates = [a for a in candidates if task.assigned_skill in a.capabilities]
            if not candidates:
                raise ValidationError("no selectable agent satisfies the task capability")
            agent = min(candidates, key=lambda candidate: str(candidate.id))
            rationale = (
                f"selected by declared capability {task.assigned_skill!r}"
                if task.assigned_skill
                else "selected first selectable agent by stable id"
            )

        if agent.company_id != company_id:
            raise NotFoundError(f"agent {agent.id} not found in company {company_id}")
        await self._tasks.update_task(
            actor,
            organization_id,
            company_id,
            task.id,
            assigned_agent_id=agent.id,
        )
        await emit(
            self._bus,
            self._store,
            EventEnvelope(
                event_type="orchestration.delegated",
                organization_id=organization_id,
                company_id=company_id,
                payload={
                    "task_id": str(task.id),
                    "agent_id": str(agent.id),
                    "rationale": rationale,
                },
            ),
        )
        return Delegation(company_id, task.id, agent.id, rationale)
