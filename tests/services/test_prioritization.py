"""PriorityEngine tests (Phase 3 Slice 3, mandate §7).

Verifies: deterministic scoring, explainable rationale, ordering by policy,
ill-defined inputs don't crash, and no dependence on model intuition.
"""

from __future__ import annotations

from typing import Any

from polsia.core.ceo.observation import CompanyObservation
from polsia.core.planning.entities import Goal, Kpi
from polsia.core.planning.values import KpiDirection, KpiStatus, Priority
from polsia.core.shared.ids import CompanyId, OrganizationId
from polsia.core.tasks.entities import Task, TaskStatus
from polsia.services.prioritization import PriorityEngine, PriorityPolicy


def make_goal(**kw: Any) -> Goal:
    return Goal.create(company_id=CompanyId.generate(), title="g", **kw)


def make_task(status: TaskStatus, goal: Goal | None = None, attempts: int = 0) -> Task:
    t = Task.create(company_id=CompanyId.generate(), title="t", max_attempts=3)
    t = _advance(t, status)
    return t


def _advance(task: Task, status: TaskStatus) -> Task:
    """Walk the task state machine to the requested status."""
    order = [
        ("ready", TaskStatus.READY),
        ("running", TaskStatus.RUNNING),
        ("failed", TaskStatus.FAILED),
        ("completed", TaskStatus.COMPLETED),
        ("blocked", TaskStatus.BLOCKED),
        ("retrying", TaskStatus.RETRYING),
    ]
    for _name, target in order:
        if task.status is target:
            break
        task = task.transition_status(target)
        if task.status is status:
            break
    return task


def make_kpi(status: KpiStatus, name: str = "k") -> Kpi:
    k = Kpi.create(
        company_id=CompanyId.generate(),
        name=name,
        direction=KpiDirection.INCREASE,
        target_value=100,
    )
    return k.record_measurement(40) if status in (KpiStatus.AT_RISK, KpiStatus.MISSED) else k


def make_obs(
    tasks: list[Task],
    *,
    ready: list[Task] | None = None,
    pending: list[Task] | None = None,
    running: list[Task] | None = None,
    failed: list[Any] | None = None,
    blocked: list[Any] | None = None,
    goals: list[Goal] | None = None,
    kpis: list[Kpi] | None = None,
) -> CompanyObservation:
    company_id = CompanyId.generate()
    obs = CompanyObservation(
        organization_id=OrganizationId.generate(),
        company_id=company_id,
        generated_at=__import__("datetime").datetime(
            2026, 9, 20, tzinfo=__import__("datetime").UTC
        ),
        active_goals=goals or [],
        goals_total=len(goals or []),
        kpis=kpis or [],
        kpis_attention=[],
        tasks_total=len(tasks),
        ready_tasks=ready or [],
        pending_tasks=pending or [],
        running_tasks=running or [],
        failed_tasks=failed or [],
        blocked_tasks=blocked or [],
        selectable_agents=[],
        pending_approvals=[],
        digest="x" * 32,
    )
    return obs


def test_no_items_when_nothing_to_do() -> None:
    engine = PriorityEngine()
    report = engine.prioritize(make_obs(tasks=[]))
    assert report.items == []


def test_priority_reflects_goal_rank() -> None:
    # a task on a CRITICAL goal outranks one on a MEDIUM goal
    critical_goal = make_goal(priority=Priority.CRITICAL)
    medium_goal = make_goal(priority=Priority.MEDIUM)
    t_critical = Task.create(company_id=CompanyId.generate(), title="c", goal_id=critical_goal.id)
    t_medium = Task.create(company_id=CompanyId.generate(), title="m", goal_id=medium_goal.id)
    obs = make_obs(
        tasks=[t_critical, t_medium],
        ready=[t_critical, t_medium],
        goals=[critical_goal, medium_goal],
    )
    engine = PriorityEngine()
    report = engine.prioritize(obs)
    assert report.items[0].task.title == "c"
    assert report.items[0].score > report.items[1].score


def test_rationale_is_explainable() -> None:
    goal = make_goal(priority=Priority.HIGH)
    task = Task.create(company_id=CompanyId.generate(), title="x", goal_id=goal.id)
    obs = make_obs(tasks=[task], ready=[task], goals=[goal])
    report = PriorityEngine().prioritize(obs)
    assert "goal_priority" in report.items[0].rationale
    assert "scored" in report.items[0].rationale


def test_failure_urgency_raises_score() -> None:
    goal = make_goal(priority=Priority.MEDIUM)
    retry_task = Task.create(company_id=CompanyId.generate(), title="r", goal_id=goal.id)
    retry_task = (
        retry_task.transition_status(TaskStatus.READY)
        .transition_status(TaskStatus.RUNNING)
        .transition_status(TaskStatus.FAILED)
    )
    from polsia.core.ceo.observation import FailedTask

    obs = make_obs(
        tasks=[retry_task],
        failed=[FailedTask(task=retry_task, last_error="e", attempts=1, can_retry=True)],
        goals=[goal],
    )
    report = PriorityEngine().prioritize(obs)
    assert report.items  # retryable failure appears
    assert "failure_urgency" in report.items[0].rationale


def test_non_retryable_failure_excluded() -> None:
    goal = make_goal()
    task = Task.create(company_id=CompanyId.generate(), title="dead", goal_id=goal.id)
    task = (
        task.transition_status(TaskStatus.READY)
        .transition_status(TaskStatus.RUNNING)
        .transition_status(TaskStatus.FAILED)
    )
    from polsia.core.ceo.observation import FailedTask

    obs = make_obs(
        tasks=[task],
        failed=[FailedTask(task=task, last_error="e", attempts=3, can_retry=False)],
        goals=[goal],
    )
    report = PriorityEngine().prioritize(obs)
    assert report.items == []  # retries exhausted -> escalate elsewhere


def test_policy_changes_ordering() -> None:
    goal = make_goal()
    t1 = Task.create(company_id=CompanyId.generate(), title="a", goal_id=goal.id)
    t2 = Task.create(company_id=CompanyId.generate(), title="b", goal_id=goal.id)
    obs = make_obs(tasks=[t1, t2], ready=[t1, t2], goals=[goal])
    # with zero weights, all scores are 0 -> stable tie order by id
    zero = PriorityEngine(
        PriorityPolicy(weight_goal=0, weight_kpi=0, weight_failure=0, weight_blocking=0)
    )
    report = zero.prioritize(obs)
    assert {i.task.title for i in report.items} == {"a", "b"}
    # default policy: deterministic, no crash, rationale present
    default = PriorityEngine().prioritize(obs)
    assert len(default.items) == 2
    assert default.policy == PriorityPolicy()


def test_ill_defined_inputs_do_not_crash() -> None:
    engine = PriorityEngine()
    # task referencing an unknown goal id -> falls back to medium rank
    orphan = Task.create(company_id=CompanyId.generate(), title="orphan", goal_id=make_goal().id)
    obs = make_obs(tasks=[orphan], ready=[orphan], goals=[])
    report = engine.prioritize(obs)
    assert len(report.items) == 1
    assert report.items[0].score is not None


def test_report_explain_is_serializable() -> None:
    import json as _json

    goal = make_goal(priority=Priority.HIGH)
    task = Task.create(company_id=CompanyId.generate(), title="x", goal_id=goal.id)
    obs = make_obs(tasks=[task], ready=[task], goals=[goal])
    report = PriorityEngine().prioritize(obs)
    payload = report.explain()
    _json.dumps(payload)
    assert payload[0]["task_id"] == str(task.id)
    assert "score" in payload[0]
