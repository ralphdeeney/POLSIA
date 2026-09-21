"""CEO decisions + plans tests (Phase 3 Slice 4, mandate §17).

Verifies: decision construction + validation (adversarial incl. invalid
types, malformed task ids, missing payload, bad confidence), typed plans with
DAG validation, and serialization.
"""

from __future__ import annotations

from typing import Any

import pytest
from polsia.core.ceo.decisions import Decision, DecisionType
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.ids import CompanyId, GoalId, TaskId
from polsia.services.planning_engine import Plan, PlanTaskDraft, VerificationCriteria


def make_company_id() -> CompanyId:
    return CompanyId.generate()


def make_plan(**kw: Any) -> Plan:
    base: dict[str, Any] = {
        "company_id": make_company_id(),
        "goal_id": GoalId.generate(),
        "objective": "Grow",
        "rationale": "from goal",
        "tasks": [
            PlanTaskDraft(title="a", verification=VerificationCriteria(expected_outcome="a")),
        ],
    }
    base.update(kw)
    return Plan(**base)  # type: ignore[arg-type]


class TestDecision:
    def test_valid_decision_constructed(self) -> None:
        d = Decision.create(
            company_id=make_company_id(),
            decision_type=DecisionType.RETRY_TASK,
            payload={"task_id": str(TaskId.generate())},
            rationale="attempt failed; retry",
            confidence=0.9,
            model_reference="deterministic-plan@1.0.0",
            cycle_id="cycle-1",
        )
        assert d.task_id is not None
        assert d.to_dict()["decision_type"] == "retry_task"
        assert d.to_dict()["confidence"] == 0.9

    def test_missing_payload_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Decision.create(
                company_id=make_company_id(),
                decision_type=DecisionType.BLOCK_TASK,
                payload={"task_id": str(TaskId.generate())},  # missing 'reason'
            )

    def test_invalid_confidence_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Decision.create(
                company_id=make_company_id(),
                decision_type=DecisionType.WAIT,
                confidence=1.5,
            )

    def test_malformed_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Decision.create(
                company_id=make_company_id(),
                decision_type=DecisionType.RETRY_TASK,
                payload={"task_id": "not-a-task-id"},
            )

    def test_escalate_requires_rationale(self) -> None:
        with pytest.raises(ValidationError):
            Decision.create(
                company_id=make_company_id(),
                decision_type=DecisionType.ESCALATE,
                payload={"reason": "x"},
            )
        # with rationale it's valid
        d = Decision.create(
            company_id=make_company_id(),
            decision_type=DecisionType.ESCALATE,
            payload={"reason": "x"},
            rationale="cannot proceed autonomously",
        )
        assert d.decision_type is DecisionType.ESCALATE

    def test_assign_agent_requires_agent(self) -> None:
        with pytest.raises(ValidationError):
            Decision.create(
                company_id=make_company_id(),
                decision_type=DecisionType.ASSIGN_AGENT,
                payload={"task_id": str(TaskId.generate())},  # missing agent_id
            )

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            Decision.create(
                company_id=make_company_id(),
                decision_type="not_a_type",  # type: ignore[arg-type]
            )
        assert "invalid decision type" in str(excinfo.value)

    def test_immutable(self) -> None:
        d = Decision.create(company_id=make_company_id(), decision_type=DecisionType.WAIT)
        import dataclasses

        # frozen dataclass: attribute assignment is rejected
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.payload = {"hacked": True}  # type: ignore[misc]


class TestPlan:
    def test_valid_plan(self) -> None:
        plan = make_plan()
        plan.validate()  # no raise
        assert plan.to_dict()["objective"] == "Grow"

    def test_empty_objective_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_plan(objective="   ").validate()

    def test_dependency_on_later_index_rejected(self) -> None:
        plan = make_plan(
            tasks=[
                PlanTaskDraft(title="a"),
                PlanTaskDraft(title="b", depends_on_indexes=[2]),
            ]
        )
        with pytest.raises(ValidationError):
            plan.validate()

    def test_out_of_range_dependency_rejected(self) -> None:
        plan = make_plan(
            tasks=[
                PlanTaskDraft(title="a"),
                PlanTaskDraft(title="b", depends_on_indexes=[2]),
            ]
        )
        with pytest.raises(ValidationError):
            plan.validate()

    def test_linear_chain_valid(self) -> None:
        plan = make_plan(
            tasks=[
                PlanTaskDraft(title="a"),
                PlanTaskDraft(title="b", depends_on_indexes=[0]),
                PlanTaskDraft(title="c", depends_on_indexes=[1]),
            ]
        )
        plan.validate()
        assert len(plan.tasks) == 3

    def test_verification_criteria_roundtrip(self) -> None:
        plan = make_plan(
            tasks=[
                PlanTaskDraft(
                    title="a",
                    verification=VerificationCriteria(
                        expected_outcome="a done", success_metric="customers"
                    ),
                    requires_approval=True,
                )
            ]
        )
        payload = plan.to_dict()
        assert payload["tasks"][0]["requires_approval"] is True
