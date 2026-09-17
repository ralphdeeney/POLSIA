"""Task domain unit tests: state machine, dependency entities, executions."""

from __future__ import annotations

import pytest
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.ids import CompanyId, TaskId
from polsia.core.tasks.entities import Task, TaskExecution
from polsia.core.tasks.values import ExecutionStatus, TaskStatus


def make_task(**kwargs: object) -> Task:
    return Task.create(company_id=CompanyId.generate(), title="Test task", **kwargs)  # type: ignore[arg-type]


class TestTaskStateMachine:
    def test_create_starts_pending(self) -> None:
        task = make_task()
        assert task.status is TaskStatus.PENDING
        assert task.attempts == 0
        assert task.max_attempts == 3

    def test_valid_lifecycle(self) -> None:
        task = make_task()
        task = task.transition_status(TaskStatus.READY)
        task = task.transition_status(TaskStatus.RUNNING)
        task = task.transition_status(TaskStatus.COMPLETED)
        assert task.status is TaskStatus.COMPLETED
        assert task.attempts == 1
        assert task.started_at is not None
        assert task.completed_at is not None

    def test_running_increments_attempts(self) -> None:
        task = make_task()
        task = task.transition_status(TaskStatus.READY).transition_status(TaskStatus.RUNNING)
        assert task.attempts == 1
        task = task.transition_status(TaskStatus.FAILED)
        task = task.transition_status(TaskStatus.RETRYING)
        task = task.transition_status(TaskStatus.RUNNING)
        assert task.attempts == 2

    def test_started_at_set_once(self) -> None:
        task = make_task()
        task = task.transition_status(TaskStatus.READY).transition_status(TaskStatus.RUNNING)
        first = task.started_at
        task = task.transition_status(TaskStatus.FAILED).transition_status(TaskStatus.RETRYING)
        task = task.transition_status(TaskStatus.RUNNING)
        assert task.started_at == first

    def test_invalid_transitions_rejected(self) -> None:
        task = make_task()
        for invalid in (
            TaskStatus.RUNNING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.RETRYING,
            TaskStatus.BLOCKED,
        ):
            with pytest.raises(ValidationError):
                task.transition_status(invalid)

    def test_failed_paths(self) -> None:
        task = make_task().transition_status(TaskStatus.READY).transition_status(TaskStatus.RUNNING)
        task = task.transition_status(TaskStatus.FAILED)
        task = task.transition_status(TaskStatus.BLOCKED)
        # BLOCKED is terminal in Phase 2
        with pytest.raises(ValidationError):
            task.transition_status(TaskStatus.RUNNING)

    def test_cancellation_paths(self) -> None:
        for path in (
            (TaskStatus.PENDING,),
            (TaskStatus.READY,),
            (TaskStatus.READY, TaskStatus.RUNNING),
        ):
            task = make_task()
            for status_value in path:
                if status_value is not TaskStatus.PENDING:
                    task = task.transition_status(status_value)
            task = task.transition_status(TaskStatus.CANCELLED)
            assert task.status is TaskStatus.CANCELLED

    def test_can_retry(self) -> None:
        task = make_task(max_attempts=2)
        task = task.transition_status(TaskStatus.READY).transition_status(TaskStatus.RUNNING)
        task = task.transition_status(TaskStatus.FAILED)
        assert task.can_retry()
        task = task.transition_status(TaskStatus.RETRYING).transition_status(TaskStatus.RUNNING)
        task = task.transition_status(TaskStatus.FAILED)
        assert not task.can_retry()  # attempts exhausted

    def test_max_attempts_validated(self) -> None:
        with pytest.raises(ValidationError):
            Task.create(company_id=CompanyId.generate(), title="t", max_attempts=0)


class TestTaskExecution:
    def test_start_defaults(self) -> None:
        execution = TaskExecution.start(
            company_id=CompanyId.generate(), task_id=TaskId.generate(), attempt_number=1
        )
        assert execution.status is ExecutionStatus.RUNNING
        assert execution.completed_at is None

    def test_complete(self) -> None:
        execution = TaskExecution.start(
            company_id=CompanyId.generate(), task_id=TaskId.generate(), attempt_number=1
        )
        done = execution.complete({"result": "ok"})
        assert done.status is ExecutionStatus.COMPLETED
        assert done.output == {"result": "ok"}
        assert done.completed_at is not None

    def test_fail(self) -> None:
        execution = TaskExecution.start(
            company_id=CompanyId.generate(), task_id=TaskId.generate(), attempt_number=1
        )
        failed = execution.fail("boom")
        assert failed.status is ExecutionStatus.FAILED
        assert failed.error == "boom"

    def test_terminal_executions_cannot_move(self) -> None:
        execution = TaskExecution.start(
            company_id=CompanyId.generate(), task_id=TaskId.generate(), attempt_number=1
        )
        done = execution.complete()
        with pytest.raises(ValidationError):
            done.fail("late error")

    def test_attempt_number_validated(self) -> None:
        with pytest.raises(ValidationError):
            TaskExecution.start(
                company_id=CompanyId.generate(), task_id=TaskId.generate(), attempt_number=0
            )

    def test_history_not_overwritten(self) -> None:
        """Execution #1 failed stays failed; execution #2 is a new record."""
        first = TaskExecution.start(
            company_id=CompanyId.generate(), task_id=TaskId.generate(), attempt_number=1
        ).fail("boom")
        second = TaskExecution.start(
            company_id=CompanyId.generate(),
            task_id=first.task_id,
            attempt_number=2,
        ).complete({"fixed": True})
        assert first.status is ExecutionStatus.FAILED
        assert first.error == "boom"
        assert second.status is ExecutionStatus.COMPLETED
        assert second.attempt_number == 2
        assert first.id != second.id
