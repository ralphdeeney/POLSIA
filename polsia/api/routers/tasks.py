"""Task endpoints, nested under organization + company (ADR-0002 scoping).

Thin HTTP surface over :class:`polsia.services.tasks.TaskService` /
:class:`polsia.services.tasks.ExecutionService`. Dependency gating and the
state machine live in the domain/services.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from polsia.api.deps import (
    CurrentUser,
    ExecutionServiceDep,
    TaskServiceDep,
    parse_agent_id,
    parse_company_id,
    parse_goal_id,
    parse_organization_id,
    parse_task_id,
)
from polsia.api.schemas import (
    ExecutionComplete,
    ExecutionFail,
    ExecutionOut,
    TaskCreate,
    TaskDependencyChange,
    TaskOut,
    TaskStatusChange,
    TaskUpdate,
)
from polsia.core.permissions.errors import ApprovalRequired
from polsia.core.planning.values import Priority
from polsia.core.shared.ids import AgentId, GoalId, TaskExecutionId, TaskId

tasks_router = APIRouter(
    prefix="/organizations/{org_id}/companies/{company_id}/tasks", tags=["tasks"]
)
executions_router = APIRouter(
    prefix="/organizations/{org_id}/companies/{company_id}/executions", tags=["executions"]
)


@tasks_router.post("", status_code=status.HTTP_201_CREATED, response_model=TaskOut)
async def create_task(
    org_id: str, company_id: str, payload: TaskCreate, user: CurrentUser, service: TaskServiceDep
) -> TaskOut:
    """Create a task (pending; becomes ready when dependencies are satisfied)."""
    task = await service.create_task(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        title=payload.title,
        description=payload.description,
        goal_id=_goal_id(payload.goal_id),
        parent_task_id=_task_id(payload.parent_task_id),
        priority=payload.priority,
        assigned_agent_id=_agent_id(payload.assigned_agent_id),
        assigned_skill=payload.assigned_skill,
        input=payload.input,
        max_attempts=payload.max_attempts,
        due_at=payload.due_at,
        metadata=payload.metadata,
        depends_on=[TaskId.parse(t) for t in (payload.depends_on or [])],
    )
    return TaskOut.model_validate(task)


@tasks_router.get("", response_model=list[TaskOut])
async def list_tasks(
    org_id: str, company_id: str, user: CurrentUser, service: TaskServiceDep
) -> list[TaskOut]:
    tasks = await service.list_tasks(
        user, parse_organization_id(org_id), parse_company_id(company_id)
    )
    return [TaskOut.model_validate(t) for t in tasks]


@tasks_router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    org_id: str, company_id: str, task_id: str, user: CurrentUser, service: TaskServiceDep
) -> TaskOut:
    task = await service.get_task(
        user, parse_organization_id(org_id), parse_company_id(company_id), TaskId.parse(task_id)
    )
    return TaskOut.model_validate(task)


@tasks_router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    org_id: str,
    company_id: str,
    task_id: str,
    payload: TaskUpdate,
    user: CurrentUser,
    service: TaskServiceDep,
) -> TaskOut:
    updates = payload.model_dump(exclude_unset=True)
    task = await service.update_task(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskId.parse(task_id),
        title=updates.get("title"),
        description=updates.get("description"),
        priority=Priority(updates["priority"]) if updates.get("priority") else None,
        assigned_agent_id=_agent_id(updates.get("assigned_agent_id")),
        assigned_skill=updates.get("assigned_skill"),
        input=updates.get("input"),
        due_at=updates.get("due_at"),
        metadata=updates.get("metadata"),
    )
    return TaskOut.model_validate(task)


@tasks_router.post("/{task_id}/status", response_model=TaskOut)
async def change_task_status(
    org_id: str,
    company_id: str,
    task_id: str,
    payload: TaskStatusChange,
    user: CurrentUser,
    service: TaskServiceDep,
) -> TaskOut:
    """Transition the task status (invalid transitions -> 422 problem)."""
    task = await service.change_status(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskId.parse(task_id),
        new_status=payload.status,
    )
    return TaskOut.model_validate(task)


@tasks_router.post("/{task_id}/dependencies", response_model=TaskOut)
async def add_dependency(
    org_id: str,
    company_id: str,
    task_id: str,
    payload: TaskDependencyChange,
    user: CurrentUser,
    service: TaskServiceDep,
) -> TaskOut:
    """Add a dependency edge (cycles and self-references -> 422/404)."""
    task = await service.add_dependency(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskId.parse(task_id),
        TaskId.parse(payload.depends_on_task_id),
    )
    return TaskOut.model_validate(task)


@tasks_router.delete("/{task_id}/dependencies/{depends_on_task_id}", response_model=TaskOut)
async def remove_dependency(
    org_id: str,
    company_id: str,
    task_id: str,
    depends_on_task_id: str,
    user: CurrentUser,
    service: TaskServiceDep,
) -> TaskOut:
    task = await service.remove_dependency(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskId.parse(task_id),
        TaskId.parse(depends_on_task_id),
    )
    return TaskOut.model_validate(task)


@tasks_router.get("/{task_id}/dependencies", response_model=list[str])
async def list_dependencies(
    org_id: str,
    company_id: str,
    task_id: str,
    user: CurrentUser,
    service: TaskServiceDep,
) -> list[str]:
    deps = await service.dependencies_of(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskId.parse(task_id),
    )
    return [str(d) for d in deps]


@tasks_router.post("/{task_id}/retry", response_model=TaskOut)
async def retry_task(
    org_id: str,
    company_id: str,
    task_id: str,
    user: CurrentUser,
    service: ExecutionServiceDep,
) -> TaskOut:
    """Retry a failed task (requires remaining attempts)."""
    task = await service.retry_task(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskId.parse(task_id),
    )
    return TaskOut.model_validate(task)


@executions_router.post(
    "/tasks/{task_id}/start", status_code=status.HTTP_201_CREATED, response_model=ExecutionOut
)
async def start_execution(
    org_id: str,
    company_id: str,
    task_id: str,
    user: CurrentUser,
    service: ExecutionServiceDep,
) -> ExecutionOut:
    """Record an authorized task attempt (authorization runs inside the service)."""
    organization_id = parse_organization_id(org_id)
    parsed_company_id = parse_company_id(company_id)
    parsed_task_id = TaskId.parse(task_id)
    try:
        _, execution = await service.start_execution(
            user, organization_id, parsed_company_id, parsed_task_id
        )
    except ApprovalRequired as exc:
        return JSONResponse(
            status_code=exc.status_code,
            media_type="application/problem+json",
            content={
                "type": "https://docs.polsia.dev/problems/approval_required",
                "title": "approval required",
                "status": exc.status_code,
                "code": exc.code,
                "detail": str(exc),
            },
        )  # type: ignore[return-value]
    return ExecutionOut.model_validate(execution)


@executions_router.get("/tasks/{task_id}", response_model=list[ExecutionOut])
async def list_executions(
    org_id: str,
    company_id: str,
    task_id: str,
    user: CurrentUser,
    service: ExecutionServiceDep,
) -> list[ExecutionOut]:
    executions = await service.list_executions(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskId.parse(task_id),
    )
    return [ExecutionOut.model_validate(e) for e in executions]


@executions_router.get("/{execution_id}", response_model=ExecutionOut)
async def get_execution(
    org_id: str,
    company_id: str,
    execution_id: str,
    user: CurrentUser,
    service: ExecutionServiceDep,
) -> ExecutionOut:
    execution = await service.get_execution(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskExecutionId.parse(execution_id),
    )
    return ExecutionOut.model_validate(execution)


@executions_router.post("/{execution_id}/complete", response_model=ExecutionOut)
async def complete_execution(
    org_id: str,
    company_id: str,
    execution_id: str,
    payload: ExecutionComplete,
    user: CurrentUser,
    service: ExecutionServiceDep,
) -> ExecutionOut:
    """Complete the running execution; the task completes with it."""
    _, execution = await service.complete_execution(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskExecutionId.parse(execution_id),
        output=payload.output,
    )
    return ExecutionOut.model_validate(execution)


@executions_router.post("/{execution_id}/fail", response_model=ExecutionOut)
async def fail_execution(
    org_id: str,
    company_id: str,
    execution_id: str,
    payload: ExecutionFail,
    user: CurrentUser,
    service: ExecutionServiceDep,
) -> ExecutionOut:
    """Fail the running execution; the task fails (or blocks)."""
    _, execution = await service.fail_execution(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        TaskExecutionId.parse(execution_id),
        error=payload.error,
    )
    return ExecutionOut.model_validate(execution)


def _goal_id(raw: str | None) -> GoalId | None:
    return parse_goal_id(raw) if raw else None


def _task_id(raw: str | None) -> TaskId | None:
    return parse_task_id(raw) if raw else None


def _agent_id(raw: str | None) -> AgentId | None:
    return parse_agent_id(raw) if raw else None
