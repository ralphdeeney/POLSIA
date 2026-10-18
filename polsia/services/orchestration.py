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

from polsia.core.ceo.triggers import SafetyGuard, TriggerDispatchContext
from polsia.core.cost.ports import UsageRecord
from polsia.core.planning.ports import KpiRepository
from polsia.core.registry.entities import Agent
from polsia.core.registry.ports import AgentRepository
from polsia.core.runtime.ports import RuntimePort, RuntimeRequest, RuntimeResult
from polsia.core.scheduling import OrchestratorPort
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import EventBus, EventEnvelope, EventStore, emit
from polsia.core.shared.ids import AgentId, CompanyId, GoalId, OrganizationId
from polsia.core.tasks.entities import Task, TaskExecution
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.execution_authorization import ExecutionAuthorizationService
from polsia.services.permissions import ApprovalService, PermissionService
from polsia.services.planning import _CompanyScope
from polsia.services.tasks import ExecutionService, TaskService
from polsia.services.usage import UsageService
from polsia.services.verification import ResultVerifier


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
        permissions: PermissionService,
        store: EventStore | None = None,
        usage_service: UsageService | None = None,
        approvals: ApprovalService | None = None,
        verifier: ResultVerifier | None = None,
        safety_guard: SafetyGuard | None = None,
        execution_authorization: ExecutionAuthorizationService | None = None,
    ) -> None:
        # SECURITY (Phase 3, mandate §10): the permission engine is a REQUIRED
        # collaborator. execute_task consults it BEFORE any execution starts;
        # without it this service cannot be constructed — the gate cannot be
        # accidentally omitted from the execution path.
        self._tasks = tasks
        self._executions = executions
        self._orchestrator = orchestrator
        self._runtime = runtime
        self._agents = agents
        self._kpis = kpis
        self._companies = companies
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus
        self._store = store
        # Optional: when wired, every execution records a UsageRecord.
        self._usage_service = usage_service
        # Optional approval service: when wired, an approval-required policy
        # opens a pending Approval (task left in its current status — the
        # state machine forbids ready→blocked); unwired, it raises like deny.
        self._verifier = verifier or ResultVerifier()
        self._safety_guard = safety_guard or SafetyGuard()
        self._execution_authorization = execution_authorization or ExecutionAuthorizationService(
            tasks=tasks,
            agents=agents,
            companies=companies,
            memberships=memberships,
            permissions=permissions,
            approvals=approvals,
        )

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

        await self._publish(
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
        return await self._execution_authorization.select_agent(
            actor, organization_id, company_id, task, agent_id=agent_id
        )

    async def authorize_execution(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: Any,
    ) -> Agent:
        """Authorize one task attempt without creating an execution or calling runtime."""
        return await self._execution_authorization.authorize(
            actor, organization_id, company_id, task_id
        )

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
        safety_context: TriggerDispatchContext | None = None,
        retry_failed: bool = False,
    ) -> tuple[Task, TaskExecution, RuntimeResult]:
        """Run one attempt of a task through the runtime and record it.

        Steps: authorize -> select agent -> PERMISSION GATE -> start execution
        (task -> running) -> runtime.execute -> complete/fail the execution
        (task completes or fails) -> emit events -> refresh KPIs.

        SECURITY INVARIANT (Phase 3, mandate §10): no execution occurs unless
        the permission engine allows it. The gate runs BEFORE the runtime is
        touched, so runtime/model output can never influence it. Deny raises
        ``PermissionDenied``; approval-required opens a pending Approval (when
        an ApprovalService is wired) and raises ``ApprovalRequired`` — in both
        cases no execution record exists afterwards.
        """
        await self._scope.require_company(actor, organization_id, company_id)
        task = await self._tasks.get_task(actor, organization_id, company_id, task_id)
        agent = await self.select_agent(actor, organization_id, company_id, task, agent_id=agent_id)

        company = await self._companies.get(organization_id, company_id)
        if company is None:
            raise ValidationError("company disappeared before execution dispatch")
        self._safety_guard.check_dispatch(company=company, task=task, context=safety_context)

        task, execution = await self._executions.start_execution(
            actor,
            organization_id,
            company_id,
            task.id,
            agent_id=agent.id,
            skill_id=task.assigned_skill,
            runtime=self._runtime.name,
            model=agent.model_reference,
            retry_failed=retry_failed,
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
        verification = self._verifier.verify(result, task.metadata.get("verification", {}))

        if result.is_success() and verification.passed:
            task, execution = await self._executions.complete_execution(
                actor, organization_id, company_id, execution.id, output=result.output
            )
        else:
            error = result.error if not result.is_success() else verification.reason
            task, execution = await self._executions.fail_execution(
                actor, organization_id, company_id, execution.id, error=error
            )
            if result.is_success():
                result = RuntimeResult.failed(
                    verification.reason,
                    runtime=result.runtime,
                    model=result.model,
                    duration_seconds=result.duration_seconds,
                    metadata={**result.metadata, "verification_failed": True},
                )

        await self._record_usage(organization_id, company_id, task, execution, result, agent)
        if verification.passed:
            await self._refresh_goal_kpis(
                actor, organization_id, company_id, task, execution, result
            )
        await self._publish(
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

    async def _record_usage(
        self,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task: Task,
        execution: TaskExecution,
        result: RuntimeResult,
        agent: Agent,
    ) -> None:
        """Record a company-scoped UsageRecord from the runtime result."""
        if self._usage_service is None:
            return
        record = UsageRecord.from_runtime_result(
            company_id=company_id,
            execution_id=execution.id,
            task_id=task.id,
            agent_id=execution.agent_id,
            runtime=execution.runtime,
            model=execution.model,
            usage=result.token_usage,
            tool_call_count=len(result.tool_calls),
            duration_seconds=result.duration_seconds,
            metadata={"status": result.status.value},
        )
        await self._usage_service.record(record, organization_id=organization_id)
        _ = agent

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
            await self._publish(
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

    async def _publish(
        self,
        event_type: str,
        organization_id: OrganizationId,
        company_id: CompanyId,
        payload: dict[str, Any],
    ) -> None:
        await emit(
            self._bus,
            self._store,
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload=payload,
            ),
        )
