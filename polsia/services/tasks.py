"""Task application services: dependency-aware task lifecycle and executions.

Key rules:
- A task becomes ``ready`` only when every dependency is COMPLETED (spec §5).
- Dependency graphs are validated for cycles and self-references before an
  edge is added.
- Task != Execution: executions are append-only attempts; completing/failing
  an execution drives the task's state machine and retry bookkeeping.
"""

from __future__ import annotations

import datetime as dt
from collections import deque
from typing import Any, cast

from polsia.core.planning.values import Priority
from polsia.core.shared.errors import (
    DomainRuleError,
    NotFoundError,
    ValidationError,
)
from polsia.core.shared.events import EventBus, EventEnvelope
from polsia.core.shared.ids import (
    AgentId,
    CompanyId,
    GoalId,
    OrganizationId,
    TaskExecutionId,
    TaskId,
)
from polsia.core.tasks.entities import Task, TaskExecution
from polsia.core.tasks.ports import (
    TaskDependencyRepository,
    TaskExecutionRepository,
    TaskRepository,
)
from polsia.core.tasks.values import TaskStatus
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.planning import _CompanyScope

TaskStatusEvent = {
    TaskStatus.READY: "task.ready",
    TaskStatus.RUNNING: "task.started",
    TaskStatus.COMPLETED: "task.completed",
    TaskStatus.FAILED: "task.failed",
    TaskStatus.RETRYING: "task.retrying",
    TaskStatus.CANCELLED: "task.cancelled",
    TaskStatus.BLOCKED: "task.blocked",
}


class TaskService:
    """Task use cases within an explicitly scoped organization + company."""

    def __init__(
        self,
        tasks: TaskRepository,
        dependencies: TaskDependencyRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
    ) -> None:
        self._tasks = tasks
        self._dependencies = dependencies
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus

    # -- commands ----------------------------------------------------------

    async def create_task(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        title: str,
        description: str | None = None,
        goal_id: GoalId | None = None,
        parent_task_id: TaskId | None = None,
        priority: Priority = Priority.MEDIUM,
        assigned_agent_id: AgentId | None = None,
        assigned_skill: str | None = None,
        input: dict[str, Any] | None = None,
        max_attempts: int = 3,
        due_at: dt.datetime | None = None,
        metadata: dict[str, Any] | None = None,
        depends_on: list[TaskId] | None = None,
    ) -> Task:
        """Create a task (pending) with optional dependencies.

        If the task has no unsatisfied dependencies it immediately becomes
        ``ready`` (and emits ``task.ready``); otherwise it stays ``pending``.
        """
        await self._scope.require_company(actor, organization_id, company_id)
        task = Task.create(
            company_id=company_id,
            title=title,
            description=description,
            goal_id=goal_id,
            parent_task_id=parent_task_id,
            priority=priority,
            assigned_agent_id=assigned_agent_id,
            assigned_skill=assigned_skill,
            input=input,
            max_attempts=max_attempts,
            due_at=due_at,
            metadata=metadata,
        )
        for prereq in depends_on or []:
            if prereq == task.id:
                raise ValidationError("a task cannot depend on itself")
            prereq_task = await self._tasks.get(company_id, prereq)
            if prereq_task is None:
                raise NotFoundError(f"dependency task {prereq} not found")
        await self._tasks.add(task)
        for prereq in depends_on or []:
            await self._dependencies.add(company_id, task.id, prereq)
        self._publish(
            "task.created",
            organization_id,
            company_id,
            task.id,
            {"title": task.title, "status": task.status.value, "priority": task.priority.value},
        )
        return await self._refresh_readiness(actor, organization_id, company_id, task)

    async def add_dependency(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        depends_on: TaskId,
    ) -> Task:
        """Add a dependency edge; validates self-reference, existence, and cycles."""
        await self._scope.require_company(actor, organization_id, company_id)
        if depends_on == task_id:
            raise ValidationError("a task cannot depend on itself")
        task = await self._load(company_id, task_id)
        prereq = await self._tasks.get(company_id, depends_on)
        if prereq is None:
            raise NotFoundError(f"dependency task {depends_on} not found")
        await self._ensure_acyclic(company_id, task_id, depends_on)
        await self._dependencies.add(company_id, task_id, depends_on)
        return await self._refresh_readiness(actor, organization_id, company_id, task)

    async def remove_dependency(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        depends_on: TaskId,
    ) -> Task:
        await self._scope.require_company(actor, organization_id, company_id)
        task = await self._load(company_id, task_id)
        await self._dependencies.remove(company_id, task_id, depends_on)
        return await self._refresh_readiness(actor, organization_id, company_id, task)

    async def change_status(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        *,
        new_status: TaskStatus,
    ) -> Task:
        """Transition the task status through the explicit state machine.

        ``pending -> ready`` additionally requires every dependency to be
        COMPLETED (the enforcement point for spec §5 dependency gating).
        """
        await self._scope.require_company(actor, organization_id, company_id)
        task = await self._load(company_id, task_id)
        if task.status is TaskStatus.PENDING and new_status is TaskStatus.READY:
            return await self._refresh_readiness(actor, organization_id, company_id, task)
        previous = task.status
        task = task.transition_status(new_status)
        await self._tasks.update(task)
        self._publish_status(organization_id, company_id, task.id, previous, new_status)
        if new_status is TaskStatus.COMPLETED:
            await self._promote_dependents(actor, organization_id, company_id, task_id)
        return task

    async def update_task(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: Priority | None = None,
        assigned_agent_id: AgentId | None = None,
        assigned_skill: str | None = None,
        input: dict[str, Any] | None = None,
        due_at: dt.datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        await self._scope.require_company(actor, organization_id, company_id)
        task = await self._load(company_id, task_id)
        if title is not None:
            task = task.rename(title)
        task = task.update(
            description=description,
            priority=priority,
            assigned_agent_id=assigned_agent_id,
            assigned_skill=assigned_skill,
            input=input,
            due_at=due_at,
            metadata=metadata,
        )
        await self._tasks.update(task)
        self._publish(
            "task.updated",
            organization_id,
            company_id,
            task.id,
            {"title": task.title, "status": task.status.value},
        )
        return task

    # -- queries -----------------------------------------------------------

    async def get_task(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId, task_id: TaskId
    ) -> Task:
        await self._scope.require_company(actor, organization_id, company_id)
        return await self._load(company_id, task_id)

    async def list_tasks(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        goal_id: GoalId | None = None,
    ) -> list[Task]:
        await self._scope.require_company(actor, organization_id, company_id)
        if goal_id is not None:
            return await self._tasks.list_for_goal(company_id, goal_id)
        return await self._tasks.list_for_company(company_id)

    async def dependencies_of(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId, task_id: TaskId
    ) -> list[TaskId]:
        await self._scope.require_company(actor, organization_id, company_id)
        await self._load(company_id, task_id)
        return await self._dependencies.dependencies_of(company_id, task_id)

    # -- internal ----------------------------------------------------------

    async def _refresh_readiness(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task: Task,
    ) -> Task:
        """Flip a pending task to ready when all its dependencies completed.

        Idempotent: a task that is already ready/running is left untouched.
        """
        if task.status is not TaskStatus.PENDING:
            return task
        deps = await self._dependencies.dependencies_of(company_id, task.id)
        for dep_id in deps:
            dep = await self._tasks.get(company_id, dep_id)
            if dep is None or dep.status is not TaskStatus.COMPLETED:
                return task
        previous = task.status
        task = task.transition_status(TaskStatus.READY)
        await self._tasks.update(task)
        self._publish_status(organization_id, company_id, task.id, previous, TaskStatus.READY)
        return task

    async def _promote_dependents(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        completed_task_id: TaskId,
    ) -> None:
        """When a task completes, pending dependents may become ready."""
        await _promote_ready_dependents(
            self._tasks,
            self._dependencies,
            self._bus,
            organization_id,
            company_id,
            completed_task_id,
        )

    async def _ensure_acyclic(
        self, company_id: CompanyId, task_id: TaskId, new_depends_on: TaskId
    ) -> None:
        """Adding task_id -> new_depends_on must not create a cycle:
        new_depends_on must not (transitively) depend on task_id."""
        edges = await self._dependencies.all_edges(company_id)
        adjacency: dict[TaskId, list[TaskId]] = {}
        for src, dst in edges:
            adjacency.setdefault(src, []).append(dst)
        visited: set[TaskId] = set()
        queue: deque[TaskId] = deque([new_depends_on])
        while queue:
            node = queue.popleft()
            if node == task_id:
                raise DomainRuleError("adding this dependency would create a cycle")
            if node in visited:
                continue
            visited.add(node)
            queue.extend(adjacency.get(node, []))

    async def _load(self, company_id: CompanyId, task_id: TaskId) -> Task:
        task = await self._tasks.get(company_id, task_id)
        if task is None:
            raise NotFoundError(f"task {task_id} not found")
        return task

    def _publish_status(
        self,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        previous: TaskStatus,
        new: TaskStatus,
    ) -> None:
        self._publish(
            TaskStatusEvent[new],
            organization_id,
            company_id,
            task_id,
            {"from": previous.value, "to": new.value},
        )

    def _publish(
        self,
        event_type: str,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        payload: dict[str, Any],
    ) -> None:
        self._bus.publish(
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload={"task_id": str(task_id), **payload},
            )
        )


class ExecutionService:
    """Execution use cases: attempts that drive the task state machine."""

    def __init__(
        self,
        tasks: TaskRepository,
        executions: TaskExecutionRepository,
        dependencies: TaskDependencyRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
    ) -> None:
        self._tasks = tasks
        self._executions = executions
        self._dependencies = dependencies
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus

    async def start_execution(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        *,
        agent_id: AgentId | None = None,
        skill_id: str | None = None,
        runtime: str | None = None,
        model: str | None = None,
        input: dict[str, Any] | None = None,
    ) -> tuple[Task, TaskExecution]:
        """Move a ready/retrying task to running and append a new execution.

        Raises ValidationError for any other source status (dependency gating
        upstream keeps unsatisfied tasks pending).
        """
        await self._scope.require_company(actor, organization_id, company_id)
        task = await self._load(company_id, task_id)
        task = task.transition_status(TaskStatus.RUNNING)
        await self._tasks.update(task)
        execution = TaskExecution.start(
            company_id=company_id,
            task_id=task_id,
            attempt_number=task.attempts,
            agent_id=agent_id if agent_id is not None else task.assigned_agent_id,
            skill_id=skill_id if skill_id is not None else task.assigned_skill,
            runtime=runtime,
            model=model,
            input=input if input is not None else dict(task.input),
        )
        await self._executions.add(execution)
        self._publish(
            "execution.started",
            organization_id,
            company_id,
            task.id,
            execution.id,
            {"attempt": execution.attempt_number},
        )
        return task, execution

    async def complete_execution(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        execution_id: TaskExecutionId,
        *,
        output: dict[str, Any] | None = None,
    ) -> tuple[Task, TaskExecution]:
        """Complete the running execution; the task completes and pending
        dependents may become ready."""
        await self._scope.require_company(actor, organization_id, company_id)
        execution = await self._load_execution(company_id, execution_id)
        execution = execution.complete(output)
        await self._executions.update(execution)
        task = await self._load(company_id, execution.task_id)
        task = task.transition_status(TaskStatus.COMPLETED).record_output(execution.output or {})
        await self._tasks.update(task)
        self._publish(
            "execution.completed",
            organization_id,
            company_id,
            task.id,
            execution.id,
            {"attempt": execution.attempt_number},
        )
        self._publish(
            TaskStatusEvent[TaskStatus.COMPLETED],
            organization_id,
            company_id,
            task.id,
            {"from": TaskStatus.RUNNING.value, "to": TaskStatus.COMPLETED.value},
        )
        await _promote_ready_dependents(
            self._tasks, self._dependencies, self._bus, organization_id, company_id, task.id
        )
        return task, execution

    async def fail_execution(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        execution_id: TaskExecutionId,
        *,
        error: str | None = None,
    ) -> tuple[Task, TaskExecution]:
        """Fail the running execution; the task fails (or blocks)."""
        await self._scope.require_company(actor, organization_id, company_id)
        execution = await self._load_execution(company_id, execution_id)
        execution = execution.fail(error)
        await self._executions.update(execution)
        task = await self._load(company_id, execution.task_id)
        task = task.transition_status(TaskStatus.FAILED)
        await self._tasks.update(task)
        self._publish(
            "execution.failed",
            organization_id,
            company_id,
            task.id,
            execution.id,
            {"attempt": execution.attempt_number, "error": error},
        )
        self._publish(
            TaskStatusEvent[TaskStatus.FAILED],
            organization_id,
            company_id,
            task.id,
            {"from": TaskStatus.RUNNING.value, "to": TaskStatus.FAILED.value},
        )
        return task, execution

    async def retry_task(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
    ) -> Task:
        """Retry a failed task (requires remaining attempts): failed ->
        retrying; the next start_execution moves it back to running."""
        await self._scope.require_company(actor, organization_id, company_id)
        task = await self._load(company_id, task_id)
        if not task.can_retry():
            raise DomainRuleError(
                f"task {task_id} cannot retry (status={task.status.value}, "
                f"attempts={task.attempts}/{task.max_attempts})"
            )
        task = task.transition_status(TaskStatus.RETRYING)
        await self._tasks.update(task)
        self._publish(
            TaskStatusEvent[TaskStatus.RETRYING],
            organization_id,
            company_id,
            task.id,
            {"from": TaskStatus.FAILED.value, "to": TaskStatus.RETRYING.value},
        )
        return task

    async def list_executions(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId, task_id: TaskId
    ) -> list[TaskExecution]:
        await self._scope.require_company(actor, organization_id, company_id)
        await self._load(company_id, task_id)
        return await self._executions.list_for_task(company_id, task_id)

    async def get_execution(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        execution_id: TaskExecutionId,
    ) -> TaskExecution:
        await self._scope.require_company(actor, organization_id, company_id)
        return await self._load_execution(company_id, execution_id)

    async def _load(self, company_id: CompanyId, task_id: TaskId) -> Task:
        task = await self._tasks.get(company_id, task_id)
        if task is None:
            raise NotFoundError(f"task {task_id} not found")
        return task

    async def _load_execution(
        self, company_id: CompanyId, execution_id: TaskExecutionId
    ) -> TaskExecution:
        execution = await self._executions.get(company_id, execution_id)
        if execution is None:
            raise NotFoundError(f"execution {execution_id} not found")
        return execution

    def _publish(
        self,
        event_type: str,
        organization_id: OrganizationId,
        company_id: CompanyId,
        task_id: TaskId,
        execution_id_or_payload: TaskExecutionId | dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Publish an execution event. Two call shapes:
        (event, org, company, task, execution_id, payload) and the status-event
        shape (event, org, company, task, payload) without an execution id."""
        if payload is None:
            body: dict[str, Any] = dict(cast("dict[str, Any]", execution_id_or_payload))
        else:
            body = {"execution_id": str(execution_id_or_payload), **payload}
        self._bus.publish(
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload={"task_id": str(task_id), **body},
            )
        )


async def _promote_ready_dependents(
    tasks: TaskRepository,
    dependencies: TaskDependencyRepository,
    bus: EventBus,
    organization_id: OrganizationId,
    company_id: CompanyId,
    completed_task_id: TaskId,
) -> None:
    """When a task completes, pending dependents whose remaining dependencies
    are all completed become ready (and emit ``task.ready``)."""
    dependents = await tasks.list_dependents(company_id, completed_task_id)
    for dep_id in dependents:
        dep = await tasks.get(company_id, dep_id)
        if dep is None or dep.status is not TaskStatus.PENDING:
            continue
        deps_of_dep = await dependencies.dependencies_of(company_id, dep_id)
        satisfied = True
        for d_id in deps_of_dep:
            d = await tasks.get(company_id, d_id)
            if d is None or d.status is not TaskStatus.COMPLETED:
                satisfied = False
                break
        if not satisfied:
            continue
        previous = dep.status
        dep = dep.transition_status(TaskStatus.READY)
        await tasks.update(dep)
        bus.publish(
            EventEnvelope(
                event_type="task.ready",
                organization_id=organization_id,
                company_id=company_id,
                payload={
                    "task_id": str(dep.id),
                    "from": previous.value,
                    "to": TaskStatus.READY.value,
                },
            )
        )
