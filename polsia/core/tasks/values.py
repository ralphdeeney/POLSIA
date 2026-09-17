"""Task domain value objects: statuses and the transition table."""

from __future__ import annotations

import enum


class TaskStatus(enum.StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


#: Allowed task transitions (spec §5). COMPLETED/CANCELLED are terminal;
#: BLOCKED has no outgoing transitions in Phase 2.
TASK_STATUS_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.READY, TaskStatus.CANCELLED}),
    TaskStatus.READY: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.FAILED: frozenset({TaskStatus.RETRYING, TaskStatus.BLOCKED}),
    TaskStatus.RETRYING: frozenset({TaskStatus.RUNNING}),
    TaskStatus.BLOCKED: frozenset(),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


class ExecutionStatus(enum.StrEnum):
    """Lifecycle of a single TaskExecution attempt."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


EXECUTION_STATUS_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.RUNNING: frozenset({ExecutionStatus.COMPLETED, ExecutionStatus.FAILED}),
    ExecutionStatus.COMPLETED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
}
