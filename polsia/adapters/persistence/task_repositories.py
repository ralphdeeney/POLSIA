"""SQLAlchemy implementations of the task-domain repository ports.

Same contract as repositories.py: no commits (the session provider owns the
transaction boundary), IntegrityError translated into domain errors,
tenant-scoped lookups, and defense-in-depth org/company checks on update.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.models import (
    TaskDependencyModel,
    TaskExecutionModel,
    TaskModel,
)
from polsia.adapters.persistence.translate import translate_integrity_error
from polsia.core.planning.values import Priority
from polsia.core.shared.errors import NotFoundError
from polsia.core.shared.ids import AgentId, CompanyId, GoalId, TaskExecutionId, TaskId
from polsia.core.tasks.entities import Task, TaskExecution
from polsia.core.tasks.values import ExecutionStatus, TaskStatus


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite stores timestamps without tzinfo; re-anchor naive values to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


def _to_task(row: TaskModel) -> Task:
    return Task(
        id=TaskId.parse(row.id),
        company_id=CompanyId.parse(row.company_id),
        title=row.title,
        description=row.description,
        goal_id=GoalId.parse(row.goal_id) if row.goal_id else None,
        parent_task_id=TaskId.parse(row.parent_task_id) if row.parent_task_id else None,
        status=TaskStatus(row.status),
        priority=Priority(row.priority),
        assigned_agent_id=AgentId.parse(row.assigned_agent_id) if row.assigned_agent_id else None,
        assigned_skill=row.assigned_skill,
        input=dict(row.input_json or {}),
        output=dict(row.output_json) if row.output_json is not None else None,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        due_at=_aware(row.due_at) if row.due_at is not None else None,
        metadata=dict(row.metadata_json or {}),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        started_at=_aware(row.started_at) if row.started_at is not None else None,
        completed_at=_aware(row.completed_at) if row.completed_at is not None else None,
    )


class SqlAlchemyTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, task: Task) -> None:
        row = TaskModel(
            id=str(task.id),
            company_id=str(task.company_id),
            goal_id=str(task.goal_id) if task.goal_id else None,
            parent_task_id=str(task.parent_task_id) if task.parent_task_id else None,
            title=task.title,
            description=task.description,
            status=task.status.value,
            priority=task.priority.value,
            assigned_agent_id=str(task.assigned_agent_id) if task.assigned_agent_id else None,
            assigned_skill=task.assigned_skill,
            input_json=dict(task.input),
            output_json=dict(task.output) if task.output is not None else None,
            attempts=task.attempts,
            max_attempts=task.max_attempts,
            due_at=task.due_at,
            metadata_json=dict(task.metadata),
            created_at=task.created_at,
            updated_at=task.updated_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, company_id: CompanyId, task_id: TaskId) -> Task | None:
        stmt = select(TaskModel).where(
            TaskModel.id == str(task_id),
            TaskModel.company_id == str(company_id),
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_task(row) if row is not None else None

    async def list_for_company(
        self,
        company_id: CompanyId,
        *,
        status: TaskStatus | None = None,
        limit: int | None = None,
    ) -> list[Task]:
        stmt = select(TaskModel).where(TaskModel.company_id == str(company_id))
        if status is not None:
            stmt = stmt.where(TaskModel.status == status.value)
        stmt = stmt.order_by(TaskModel.created_at, TaskModel.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._session.scalars(stmt)).all()
        return [_to_task(row) for row in rows]

    async def count_for_company(self, company_id: CompanyId) -> int:
        stmt = (
            select(func.count())
            .select_from(TaskModel)
            .where(TaskModel.company_id == str(company_id))
        )
        return int(await self._session.scalar(stmt) or 0)

    async def list_for_goal(self, company_id: CompanyId, goal_id: GoalId) -> list[Task]:
        stmt = (
            select(TaskModel)
            .where(
                TaskModel.company_id == str(company_id),
                TaskModel.goal_id == str(goal_id),
            )
            .order_by(TaskModel.created_at, TaskModel.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_task(row) for row in rows]

    async def list_dependents(self, company_id: CompanyId, task_id: TaskId) -> list[TaskId]:
        stmt = select(TaskDependencyModel.task_id).where(
            TaskDependencyModel.company_id == str(company_id),
            TaskDependencyModel.depends_on_task_id == str(task_id),
        )
        rows = (await self._session.scalars(stmt)).all()
        return [TaskId.parse(r) for r in rows]

    async def update(self, task: Task) -> None:
        row = await self._session.get(TaskModel, str(task.id))
        if row is None or row.company_id != str(task.company_id):
            raise NotFoundError(f"task {task.id} not found")
        row.goal_id = str(task.goal_id) if task.goal_id else None
        row.parent_task_id = str(task.parent_task_id) if task.parent_task_id else None
        row.title = task.title
        row.description = task.description
        row.status = task.status.value
        row.priority = task.priority.value
        row.assigned_agent_id = str(task.assigned_agent_id) if task.assigned_agent_id else None
        row.assigned_skill = task.assigned_skill
        row.input_json = dict(task.input)
        row.output_json = dict(task.output) if task.output is not None else None
        row.attempts = task.attempts
        row.max_attempts = task.max_attempts
        row.due_at = task.due_at
        row.metadata_json = dict(task.metadata)
        row.updated_at = task.updated_at
        row.started_at = task.started_at
        row.completed_at = task.completed_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def start_attempt(self, expected: Task, running: Task) -> bool:
        """Compare-and-set one authorized attempt against the checked snapshot."""
        result = await self._session.execute(
            update(TaskModel)
            .where(
                TaskModel.id == str(expected.id),
                TaskModel.company_id == str(expected.company_id),
                TaskModel.status == expected.status.value,
                TaskModel.attempts == expected.attempts,
                TaskModel.updated_at == expected.updated_at,
            )
            .values(
                status=running.status.value,
                attempts=running.attempts,
                assigned_agent_id=(
                    str(running.assigned_agent_id) if running.assigned_agent_id else None
                ),
                updated_at=running.updated_at,
                started_at=running.started_at,
            )
        )
        await self._session.flush()
        return isinstance(result, CursorResult) and result.rowcount == 1


class SqlAlchemyTaskDependencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, company_id: CompanyId, task_id: TaskId, depends_on: TaskId) -> None:
        row = TaskDependencyModel(
            task_id=str(task_id),
            depends_on_task_id=str(depends_on),
            company_id=str(company_id),
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def dependencies_of(self, company_id: CompanyId, task_id: TaskId) -> list[TaskId]:
        stmt = select(TaskDependencyModel.depends_on_task_id).where(
            TaskDependencyModel.company_id == str(company_id),
            TaskDependencyModel.task_id == str(task_id),
        )
        rows = (await self._session.scalars(stmt)).all()
        return [TaskId.parse(r) for r in rows]

    async def all_edges(self, company_id: CompanyId) -> list[tuple[TaskId, TaskId]]:
        stmt = select(TaskDependencyModel).where(TaskDependencyModel.company_id == str(company_id))
        rows = (await self._session.scalars(stmt)).all()
        return [(TaskId.parse(r.task_id), TaskId.parse(r.depends_on_task_id)) for r in rows]

    async def remove(self, company_id: CompanyId, task_id: TaskId, depends_on: TaskId) -> None:
        await self._session.execute(
            delete(TaskDependencyModel).where(
                TaskDependencyModel.company_id == str(company_id),
                TaskDependencyModel.task_id == str(task_id),
                TaskDependencyModel.depends_on_task_id == str(depends_on),
            )
        )
        await self._session.flush()


def _to_execution(row: TaskExecutionModel) -> TaskExecution:
    return TaskExecution(
        id=TaskExecutionId.parse(row.id),
        company_id=CompanyId.parse(row.company_id),
        task_id=TaskId.parse(row.task_id),
        attempt_number=row.attempt_number,
        status=ExecutionStatus(row.status),
        agent_id=AgentId.parse(row.agent_id) if row.agent_id else None,
        skill_id=row.skill_id,
        runtime=row.runtime,
        model=row.model,
        input=dict(row.input_json or {}),
        output=dict(row.output_json) if row.output_json is not None else None,
        error=row.error,
        started_at=_aware(row.started_at),
        completed_at=_aware(row.completed_at) if row.completed_at is not None else None,
        metadata=dict(row.metadata_json or {}),
    )


class SqlAlchemyTaskExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, execution: TaskExecution) -> None:
        row = TaskExecutionModel(
            id=str(execution.id),
            company_id=str(execution.company_id),
            task_id=str(execution.task_id),
            attempt_number=execution.attempt_number,
            status=execution.status.value,
            agent_id=str(execution.agent_id) if execution.agent_id else None,
            skill_id=execution.skill_id,
            runtime=execution.runtime,
            model=execution.model,
            input_json=dict(execution.input),
            output_json=dict(execution.output) if execution.output is not None else None,
            error=execution.error,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
            metadata_json=dict(execution.metadata),
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(
        self, company_id: CompanyId, execution_id: TaskExecutionId
    ) -> TaskExecution | None:
        stmt = select(TaskExecutionModel).where(
            TaskExecutionModel.id == str(execution_id),
            TaskExecutionModel.company_id == str(company_id),
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_execution(row) if row is not None else None

    async def list_for_task(self, company_id: CompanyId, task_id: TaskId) -> list[TaskExecution]:
        stmt = (
            select(TaskExecutionModel)
            .where(
                TaskExecutionModel.company_id == str(company_id),
                TaskExecutionModel.task_id == str(task_id),
            )
            .order_by(TaskExecutionModel.attempt_number, TaskExecutionModel.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_execution(row) for row in rows]

    async def update(self, execution: TaskExecution) -> None:
        row = await self._session.get(TaskExecutionModel, str(execution.id))
        if row is None or row.company_id != str(execution.company_id):
            raise NotFoundError(f"execution {execution.id} not found")
        row.status = execution.status.value
        row.agent_id = str(execution.agent_id) if execution.agent_id else None
        row.skill_id = execution.skill_id
        row.runtime = execution.runtime
        row.model = execution.model
        row.output_json = dict(execution.output) if execution.output is not None else None
        row.error = execution.error
        row.completed_at = execution.completed_at
        row.metadata_json = dict(execution.metadata)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc
