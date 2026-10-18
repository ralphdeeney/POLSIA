"""Repository ports for the task domain (company-scoped, ADR-0002)."""

from __future__ import annotations

from typing import Protocol

from polsia.core.shared.ids import CompanyId, GoalId, TaskExecutionId, TaskId
from polsia.core.tasks.entities import Task, TaskExecution
from polsia.core.tasks.values import TaskStatus


class TaskRepository(Protocol):
    async def add(self, task: Task) -> None: ...
    async def get(self, company_id: CompanyId, task_id: TaskId) -> Task | None: ...
    async def list_for_company(
        self,
        company_id: CompanyId,
        *,
        status: TaskStatus | None = None,
        limit: int | None = None,
    ) -> list[Task]:
        """Company-scoped tasks in deterministic order.

        ``status`` filters and ``limit`` bounds the query so observation never
        materializes an unbounded task table (tenant-scoped, indexed reads).
        """
        ...

    async def count_for_company(self, company_id: CompanyId) -> int:
        """Total tasks for the company (tenant-scoped) without fetching rows."""
        ...

    async def list_for_goal(self, company_id: CompanyId, goal_id: GoalId) -> list[Task]: ...
    async def list_dependents(self, company_id: CompanyId, task_id: TaskId) -> list[TaskId]: ...
    async def update(self, task: Task) -> None: ...
    async def start_attempt(self, expected: Task, running: Task) -> bool:
        """Atomically persist one authorized transition to running."""
        ...


class TaskDependencyRepository(Protocol):
    """Adjacency store: (task depends_on prerequisite)."""

    async def add(self, company_id: CompanyId, task_id: TaskId, depends_on: TaskId) -> None: ...
    async def dependencies_of(self, company_id: CompanyId, task_id: TaskId) -> list[TaskId]: ...
    async def all_edges(self, company_id: CompanyId) -> list[tuple[TaskId, TaskId]]: ...
    async def remove(self, company_id: CompanyId, task_id: TaskId, depends_on: TaskId) -> None: ...


class TaskExecutionRepository(Protocol):
    async def add(self, execution: TaskExecution) -> None: ...
    async def get(
        self, company_id: CompanyId, execution_id: TaskExecutionId
    ) -> TaskExecution | None: ...
    async def list_for_task(
        self, company_id: CompanyId, task_id: TaskId
    ) -> list[TaskExecution]: ...
    async def update(self, execution: TaskExecution) -> None: ...
