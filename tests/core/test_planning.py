"""Planning domain unit tests: invariants, state machines, KPI evaluator."""

from __future__ import annotations

import datetime as dt

import pytest
from polsia.core.planning.entities import Goal, Kpi
from polsia.core.planning.values import (
    GoalStatus,
    KpiDirection,
    KpiStatus,
    Priority,
    evaluate_kpi_status,
)
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.ids import CompanyId, GoalId


def make_goal(**kwargs: object) -> Goal:
    return Goal.create(company_id=CompanyId.generate(), title="Test goal", **kwargs)  # type: ignore[arg-type]


class TestGoalInvariants:
    def test_create_defaults(self) -> None:
        goal = make_goal()
        assert goal.status is GoalStatus.DRAFT
        assert goal.priority is Priority.MEDIUM
        assert goal.parent_goal_id is None
        assert goal.metadata == {}

    def test_blank_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Goal.create(company_id=CompanyId.generate(), title="   ")

    def test_rename_blank_rejected(self) -> None:
        goal = make_goal()
        with pytest.raises(ValidationError):
            goal.rename("   ")

    def test_metadata_is_copied(self) -> None:
        meta = {"k": "v"}
        goal = make_goal(metadata=meta)
        meta["k"] = "changed"
        assert goal.metadata == {"k": "v"}


class TestGoalTransitions:
    def test_valid_lifecycle(self) -> None:
        goal = make_goal()
        goal = goal.transition_status(GoalStatus.ACTIVE)
        goal = goal.transition_status(GoalStatus.PAUSED)
        goal = goal.transition_status(GoalStatus.ACTIVE)
        goal = goal.transition_status(GoalStatus.COMPLETED)
        assert goal.status is GoalStatus.COMPLETED

    def test_invalid_transitions_rejected(self) -> None:
        goal = make_goal()
        for invalid in (GoalStatus.PAUSED, GoalStatus.COMPLETED):
            with pytest.raises(ValidationError):
                goal.transition_status(invalid)

    def test_terminal_states(self) -> None:
        for terminal in (GoalStatus.COMPLETED, GoalStatus.CANCELLED):
            goal = make_goal().transition_status(GoalStatus.ACTIVE)
            goal = goal.transition_status(terminal)
            with pytest.raises(ValidationError):
                goal.transition_status(GoalStatus.ACTIVE)

    def test_cancel_paths(self) -> None:
        for start in (GoalStatus.DRAFT, GoalStatus.ACTIVE, GoalStatus.PAUSED):
            goal = make_goal()
            if start is GoalStatus.PAUSED:
                goal = goal.transition_status(GoalStatus.ACTIVE).transition_status(start)
            elif start is GoalStatus.ACTIVE:
                goal = goal.transition_status(start)
            goal = goal.transition_status(GoalStatus.CANCELLED)
            assert goal.status is GoalStatus.CANCELLED


class TestKpiEvaluator:
    def test_not_started(self) -> None:
        assert (
            evaluate_kpi_status(direction=KpiDirection.INCREASE, current=None, target=10)
            is KpiStatus.NOT_STARTED
        )

    def test_increase_achieved(self) -> None:
        assert (
            evaluate_kpi_status(direction=KpiDirection.INCREASE, current=10, target=10)
            is KpiStatus.ACHIEVED
        )
        assert (
            evaluate_kpi_status(direction=KpiDirection.INCREASE, current=15, target=10)
            is KpiStatus.ACHIEVED
        )

    def test_increase_bands(self) -> None:
        assert (
            evaluate_kpi_status(direction=KpiDirection.INCREASE, current=8, target=10)
            is KpiStatus.ON_TRACK
        )
        assert (
            evaluate_kpi_status(direction=KpiDirection.INCREASE, current=2, target=10)
            is KpiStatus.AT_RISK
        )

    def test_decrease(self) -> None:
        assert (
            evaluate_kpi_status(direction=KpiDirection.DECREASE, current=2, target=2)
            is KpiStatus.ACHIEVED
        )
        assert (
            evaluate_kpi_status(direction=KpiDirection.DECREASE, current=1, target=2)
            is KpiStatus.ACHIEVED
        )
        assert (
            evaluate_kpi_status(direction=KpiDirection.DECREASE, current=2.4, target=2)
            is KpiStatus.ON_TRACK
        )
        assert (
            evaluate_kpi_status(direction=KpiDirection.DECREASE, current=20, target=2)
            is KpiStatus.AT_RISK
        )

    def test_maintain(self) -> None:
        assert (
            evaluate_kpi_status(direction=KpiDirection.MAINTAIN, current=100, target=100)
            is KpiStatus.ACHIEVED
        )
        assert (
            evaluate_kpi_status(direction=KpiDirection.MAINTAIN, current=95, target=100)
            is KpiStatus.ACHIEVED
        )
        assert (
            evaluate_kpi_status(direction=KpiDirection.MAINTAIN, current=88, target=100)
            is KpiStatus.ON_TRACK
        )
        assert (
            evaluate_kpi_status(direction=KpiDirection.MAINTAIN, current=50, target=100)
            is KpiStatus.AT_RISK
        )

    def test_missed_after_deadline(self) -> None:
        assert (
            evaluate_kpi_status(
                direction=KpiDirection.INCREASE, current=3, target=10, deadline_passed=True
            )
            is KpiStatus.MISSED
        )
        # achieved beats missed even after the deadline
        assert (
            evaluate_kpi_status(
                direction=KpiDirection.INCREASE, current=10, target=10, deadline_passed=True
            )
            is KpiStatus.ACHIEVED
        )

    def test_invalid_bands_rejected(self) -> None:
        with pytest.raises(ValueError):
            evaluate_kpi_status(direction=KpiDirection.INCREASE, current=1, target=1, risk_band=1.5)
        with pytest.raises(ValueError):
            evaluate_kpi_status(direction=KpiDirection.INCREASE, current=1, target=1, tolerance=0)


class TestKpiEntity:
    def test_record_measurement_updates_status(self) -> None:
        kpi = Kpi.create(
            company_id=CompanyId.generate(),
            name="customers",
            direction=KpiDirection.INCREASE,
            target_value=10,
        )
        assert kpi.status is KpiStatus.NOT_STARTED
        measured = kpi.record_measurement(8)
        assert measured.current_value == 8
        assert measured.status is KpiStatus.ON_TRACK
        assert measured.updated_at >= kpi.updated_at

    def test_mutation_is_immutable(self) -> None:
        kpi = Kpi.create(
            company_id=CompanyId.generate(),
            name="mrr",
            direction=KpiDirection.INCREASE,
            target_value=100,
        )
        measured = kpi.record_measurement(10)
        assert kpi.current_value is None
        assert measured.current_value == 10

    def test_due_at_is_preserved(self) -> None:
        due = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
        goal = make_goal(due_at=due)
        assert goal.due_at == due
        assert isinstance(goal.id, GoalId)
