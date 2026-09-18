"""Orchestration service (Phase 2): the kernel flow, contract-level.

Ties the primitives together without autonomous reasoning:

    Goal -> decomposition (OrchestratorPort) -> Tasks (with dependencies)
         -> agent selection -> permission check -> runtime execution
         -> Execution record -> events -> KPI refresh

The service composes existing domain services (TaskService, ExecutionService,
KpiService) and ports; it adds no new business semantics.
"""

from __future__ import annotations

from typing import Any

from polsia.core.planning.ports import KpiRepository
from polsia.core.registry.entities import Agent
from polsia.core.registry.ports import AgentRepository
from polsia.core.runtime.ports import RuntimePort, RuntimeRequest, RuntimeResult
from polsia.core.scheduling import OrchestratorPort
from polsia.core.shared.errors import NotFoundError, ValidationError
from polsia.core.shared.events import EventBus, EventEnvelope
from polsia.core.shared.ids import AgentId, CompanyId, GoalId, OrganizationId
from polsia.core.tasks.entities import Task, TaskExecution
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.planning import _CompanyScope
from polsia.services.tasks import ExecutionService, TaskService


class OrchestrationService:
    """Goal -> tasks -> execution, using the injected ports.

    Phase 2 scope: deterministic decomposition, explicit agent selection
    (caller-named or first selectable agent), one runtime attempt per call.
    Planning intelligence, retries-with-strategy and multi-agent collaboration
    belong to later phases.
    """

    def __init__(
        self,
        *,
        tasks: TaskService,
        executions: ExecutionService,
        orchestrator: OrchestratorPort,
        runtime: RuntimePort,
        agents: AgentRepository,
        kpis: KpiRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
    ) -> None:
        self._tasks = tasks
        self._executions = executions
        self._orchestrator = orchestrator
        self._runtime = runtime
        self._agents = agents
        self._kpis = kpis
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus

    async def plan_goal(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        goal_id: GoalId,
        *,
        goal_title: str,
        goal_description: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[Task]:
        """Decompose a goal into persisted tasks (with dependency wiring).

        Task drafts referencing earlier drafts are mapped to the created task
        ids, so a sequential plan becomes a real dependency chain that the
        readiness rules already enforce.
        """
        await self._scope.require_company(actor, organization_id, company_id)
        plan = await self._orchestrator.decompose(
            company_id=company_id,
            goal_id=goal_id,
            goal_title=goal_title,
            goal_description=goal_description,
            context=context,
        )
        if not plan.tasks:
            raise ValidationError("orchestrator produced no tasks for the goal")

        created: list[Task] = []
        for draft in plan.tasks:
            depends_on = [
                created[index].id for index in draft.depends_on_indexes if index < len(created)
            ]
            task = await self._tasks.create_task(
                actor,
                organization_id,
                company_id,
                title=draft.title,
                description=draft.description,
                goal_id=goal_id,
                assigned_skill=draft.assigned_skill,
                input=draft.input,
                metadata=draft.metadata,
                depends_on=depends_on,
            )
            created.append(task)

        self._publish(
            "orchestration.planned",
            organization_id,
            company_id,
            {
                "goal_id": str(goal_id),
                "task_count": len(created),
                "task_ids": [str(t.id) for t in created],
                "rationale": plan.rationale,
            },
        )
        return created

    async def select_agent(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task: Task,
        *,
        agent_id: AgentId | None = None,
    ) -> Agent:
        """Pick the agent for a task: an explicit id, the task's assignment,
        or the first selectable (active) agent in the company."""
        await self._scope.require_company(actor, organization_id, company_id)
        candidate_id = agent_id or task.assigned_agent_id
        if candidate_id is not None:
            agent = await self._agents.get(company_id, candidate_id)
            if agent is None:
                raise NotFoundError(f"agent {candidate_id} not found")
            if not agent.is_selectable():
                raise ValidationError(f"agent {candidate_id} is not selectable (disabled)")
            return agent
        for agent in await self._agents.list_for_company(company_id):
            if agent.is_selectable():
                return agent
        raise ValidationError("no selectable agent is registered for this company")

    async def execute_task(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: Any,
        *,
        tool_permissions: dict[str, str] | None = None,
        context: dict[str, Any] | None = None,
        agent_id: AgentId | None = None,
    ) -> tuple[Task, TaskExecution, RuntimeResult]:
        """Run one attempt of a task through the runtime and record it.

        Steps: authorize -> select agent -> start execution (task -> running)
        -> runtime.execute -> complete/fail the execution (task completes or
        fails) -> emit events -> refresh KPIs attached to the task's goal.
        """
        await self._scope.require_company(actor, organization_id, company_id)
        task = await self._tasks.get_task(actor, organization_id, company_id, task_id)
        agent = await self.select_agent(actor, organization_id, company_id, task, agent_id=agent_id)

        task, execution = await self._executions.start_execution(
            actor,
            organization_id,
            company_id,
            task.id,
            agent_id=agent.id,
            skill_id=task.assigned_skill,
            runtime=self._runtime.name,
            model=agent.model_reference,
        )

        request = RuntimeRequest(
            company_id=company_id,
            task_id=task.id,
            agent_id=agent.id,
            skill_id=task.assigned_skill,
            runtime=self._runtime.name,
            model=agent.model_reference,
            input=dict(task.input),
            context=dict(context or {}),
            tool_permissions=dict(tool_permissions or {}),
            system_instructions=agent.system_instructions,
        )
        result = await self._runtime.execute(request)

        if result.is_success():
            task, execution = await self._executions.complete_execution(
                actor, organization_id, company_id, execution.id, output=result.output
            )
        else:
            task, execution = await self._executions.fail_execution(
                actor, organization_id, company_id, execution.id, error=result.error
            )

        await self._refresh_goal_kpis(actor, organization_id, company_id, task, execution, result)
        self._publish(
            "orchestration.executed",
            organization_id,
            company_id,
            {
                "task_id": str(task.id),
                "execution_id": str(execution.id),
                "agent_id": str(agent.id),
                "runtime": self._runtime.name,
                "status": result.status.value,
                "duration_seconds": result.duration_seconds,
                "total_tokens": result.token_usage.total_tokens,
                "tool_calls": [call.tool for call in result.tool_calls],
            },
        )
        return task, execution, result

    async def _refresh_goal_kpis(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task: Task,
        execution: TaskExecution,
        result: RuntimeResult,
    ) -> None:
        """Generic post-execution measurement hook.

        If the task's input declares a ``kpi_metric`` (a generic numeric
        measurement) and the goal has KPIs attached, record it. The OS does not
        interpret what the metric means."""
        metric = result.output.get("kpi_value")
        if metric is None or task.goal_id is None:
            return
        try:
            value = float(metric)
        except (TypeError, ValueError):
            return
        kpis = await self._kpis.list_for_goal(company_id, task.goal_id)
        if not kpis:
            return
        for kpi in kpis:
            updated = kpi.record_measurement(value)
            await self._kpis.update(updated)
            self._publish(
                "kpi.measurement_recorded",
                organization_id,
                company_id,
                {
                    "kpi_id": str(kpi.id),
                    "current": updated.current_value,
                    "status": updated.status.value,
                    "source_execution_id": str(execution.id),
                },
            )

    def _publish(
        self,
        event_type: str,
        organization_id: OrganizationId,
        company_id: CompanyId,
        payload: dict[str, Any],
    ) -> None:
        self._bus.publish(
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload=payload,
            )
        )
