"""Task and TaskExecution domain entities (company-scoped, frozen dataclasses)."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from polsia.core.planning.values import Priority
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import utcnow
from polsia.core.shared.ids import (
    AgentId,
    CompanyId,
    GoalId,
    TaskExecutionId,
    TaskId,
)
from polsia.core.tasks.values import (
    EXECUTION_STATUS_TRANSITIONS,
    ExecutionStatus,
    TaskStatus,
)


def _require_title(title: str) -> str:
    if not title.strip():
        raise ValidationError("title must not be empty")
    return title.strip()


@dataclasses.dataclass(frozen=True)
class Task:
    """Intended work in a company. What needs to happen — not how or by whom
    (assignment fields are hints for the orchestrator; executions are the
    attempts)."""

    id: TaskId
    company_id: CompanyId
    title: str
    description: str | None = None
    goal_id: GoalId | None = None
    parent_task_id: TaskId | None = None
    status: TaskStatus = TaskStatus.PENDING
    priority: Priority = Priority.MEDIUM
    assigned_agent_id: AgentId | None = None
    assigned_skill: str | None = None  # free-form skill reference, e.g. "name@1.0.0"
    input: dict[str, Any] = dataclasses.field(default_factory=dict)
    output: dict[str, Any] | None = None
    attempts: int = 0
    max_attempts: int = 3
    due_at: dt.datetime | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None

    @staticmethod
    def create(
        *,
        company_id: CompanyId,
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
        now: dt.datetime | None = None,
    ) -> Task:
        if max_attempts < 1:
            raise ValidationError("max_attempts must be >= 1")
        ts = now or utcnow()
        return Task(
            id=TaskId.generate(),
            company_id=company_id,
            title=_require_title(title),
            description=description,
            goal_id=goal_id,
            parent_task_id=parent_task_id,
            status=TaskStatus.PENDING,
            priority=priority,
            assigned_agent_id=assigned_agent_id,
            assigned_skill=assigned_skill,
            input=dict(input or {}),
            output=None,
            attempts=0,
            max_attempts=max_attempts,
            due_at=due_at,
            metadata=dict(metadata or {}),
            created_at=ts,
            updated_at=ts,
        )

    def _mutated(self, **changes: Any) -> Task:
        return dataclasses.replace(self, updated_at=utcnow(), **changes)

    def rename(self, title: str) -> Task:
        return self._mutated(title=_require_title(title))

    def update(
        self,
        *,
        description: str | None = None,
        priority: Priority | None = None,
        assigned_agent_id: AgentId | None = None,
        assigned_skill: str | None = None,
        input: dict[str, Any] | None = None,
        due_at: dt.datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        changes: dict[str, Any] = {}
        if description is not None:
            changes["description"] = description
        if priority is not None:
            changes["priority"] = priority
        if assigned_agent_id is not None:
            changes["assigned_agent_id"] = assigned_agent_id
        if assigned_skill is not None:
            changes["assigned_skill"] = assigned_skill
        if input is not None:
            changes["input"] = dict(input)
        if due_at is not None:
            changes["due_at"] = due_at
        if metadata is not None:
            changes["metadata"] = dict(metadata)
        return self._mutated(**changes)

    def transition_status(self, new_status: TaskStatus, *, now: dt.datetime | None = None) -> Task:
        if new_status not in self._allowed():
            allowed = ", ".join(sorted(s.value for s in self._allowed()))
            raise ValidationError(
                f"task {self.status.value!r} cannot transition to {new_status.value!r} "
                f"(allowed: {allowed or 'none — terminal'})"
            )
        changes: dict[str, Any] = {"status": new_status}
        ts = now or utcnow()
        if new_status is TaskStatus.RUNNING:
            # Every run counts as an attempt (first run and retries alike).
            changes["attempts"] = self.attempts + 1
            if self.started_at is None:
                changes["started_at"] = ts
        if new_status is TaskStatus.COMPLETED:
            changes["completed_at"] = ts
        return self._mutated(**changes)

    def _allowed(self) -> frozenset[TaskStatus]:
        from polsia.core.tasks.values import TASK_STATUS_TRANSITIONS

        return TASK_STATUS_TRANSITIONS[self.status]

    def can_retry(self) -> bool:
        """A failed task may retry while attempts remain."""
        return self.status is TaskStatus.FAILED and self.attempts < self.max_attempts

    def record_output(self, output: dict[str, Any]) -> Task:
        return self._mutated(output=dict(output))


@dataclasses.dataclass(frozen=True)
class TaskExecution:
    """One attempt to perform a Task. Append-only: an execution is created
    running and only ever moves to completed/failed. History is immutable."""

    id: TaskExecutionId
    company_id: CompanyId
    task_id: TaskId
    attempt_number: int
    status: ExecutionStatus = ExecutionStatus.RUNNING
    agent_id: AgentId | None = None
    skill_id: str | None = None
    runtime: str | None = None
    model: str | None = None
    input: dict[str, Any] = dataclasses.field(default_factory=dict)
    output: dict[str, Any] | None = None
    error: str | None = None
    started_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    completed_at: dt.datetime | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    @staticmethod
    def start(
        *,
        company_id: CompanyId,
        task_id: TaskId,
        attempt_number: int,
        agent_id: AgentId | None = None,
        skill_id: str | None = None,
        runtime: str | None = None,
        model: str | None = None,
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> TaskExecution:
        if attempt_number < 1:
            raise ValidationError("attempt_number must be >= 1")
        return TaskExecution(
            id=TaskExecutionId.generate(),
            company_id=company_id,
            task_id=task_id,
            attempt_number=attempt_number,
            status=ExecutionStatus.RUNNING,
            agent_id=agent_id,
            skill_id=skill_id,
            runtime=runtime,
            model=model,
            input=dict(input or {}),
            started_at=now or utcnow(),
            metadata=dict(metadata or {}),
        )

    def complete(
        self, output: dict[str, Any] | None = None, *, now: dt.datetime | None = None
    ) -> TaskExecution:
        finished = self._finish(ExecutionStatus.COMPLETED, now)
        return dataclasses.replace(finished, output=dict(output or {}))

    def fail(self, error: str | None = None, *, now: dt.datetime | None = None) -> TaskExecution:
        finished = self._finish(ExecutionStatus.FAILED, now)
        return dataclasses.replace(finished, error=error)

    def _finish(self, new_status: ExecutionStatus, now: dt.datetime | None) -> TaskExecution:
        if new_status not in EXECUTION_STATUS_TRANSITIONS[self.status]:
            raise ValidationError(
                f"execution {self.status.value!r} cannot transition to {new_status.value!r}"
            )
        return dataclasses.replace(self, status=new_status, completed_at=now or utcnow())
