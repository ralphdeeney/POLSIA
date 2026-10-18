"""Evaluation framework tests (mandate §9/§10/§13)."""

from __future__ import annotations

from polsia.adapters.decision import (
    DeterministicAdvisoryBaseline,
    LayaTestDouble,
)
from polsia.eval import dataset_fingerprint, evaluate_classifier
from polsia.eval.agent import SCENARIOS, evaluate_agent_runtime, run_agent_scenario
from polsia.eval.dataset import ROUTE_DATASET_FINGERPRINT, ROUTE_EXAMPLES
from polsia.eval.routing import run_routing_comparison


class TestRoutingEvaluation:
    def test_dataset_is_representative_and_stable(self) -> None:
        labels = {label for _, label in ROUTE_EXAMPLES}
        assert {
            "simple",
            "research",
            "engineering",
            "marketing",
            "sales",
            "support",
            "finance",
            "multi_agent",
            "adversarial",
            "unknown",
        } <= labels
        assert ROUTE_DATASET_FINGERPRINT == dataset_fingerprint(ROUTE_EXAMPLES)

    def test_deterministic_baseline_scores_perfect_on_known_lexicons(self) -> None:
        metrics, items = evaluate_classifier(DeterministicAdvisoryBaseline(), ROUTE_EXAMPLES)
        assert metrics.accuracy == 1.0
        assert metrics.unsafe_route_rate == 0.0
        # adversarial inputs must always escalate
        adversarial = [i for i in items if i["expected"] == "adversarial"]
        assert all(i["escalate"] for i in adversarial)
        # ambiguous inputs must abstain
        ambiguous = [i for i in items if i["expected"] == "unknown"]
        assert all(i["abstain"] for i in ambiguous)
        assert metrics.confusion  # the matrix is materialized, not hidden

    def test_routing_comparison_runs_deterministic_and_marks_laya_not_run(self) -> None:
        report = run_routing_comparison()
        assert [arm.arm for arm in report.arms] == ["A"]
        assert report.laya_status.startswith("NOT RUN")
        assert report.recommended_default == "deterministic_baseline"
        # the harness CAN wire laya: the arm then materializes with a test double
        wired = run_routing_comparison(laya_backend=LayaTestDouble({"invoice": ("finance", 0.9)}))
        assert {arm.arm for arm in wired.arms} == {"A", "B"}
        # recorded evidence keeps the same reporting shape for both arms
        assert wired.arms[1].metrics.total == report.arms[0].metrics.total


class TestAgentEvaluation:
    def test_all_scripted_scenarios_pass(self) -> None:
        report = evaluate_agent_runtime()
        failed = [r for r in report.scenarios if not r.passed]
        assert not failed, [r.scenario for r in failed]
        assert report.verified_completion_rate == 1.0
        assert report.tool_selection_correctness == 1.0
        assert report.security_violations == 0
        assert report.budget_violations == 0
        assert report.unverifiable_claims_rejected >= 1
        assert report.dataset_note.startswith("scripted deterministic")

    def test_scenario_report_shape(self) -> None:
        result = run_agent_scenario(SCENARIOS[0])
        assert result.passed
        assert result.tool_sequence == ("read_file",)
        assert result.runtime_state == "completed"
        assert result.tokens >= 0

    def test_reports_are_sanitized(self) -> None:
        """Reports must not embed tool root paths or token values beyond bounds."""
        report = evaluate_agent_runtime().to_dict()
        blob = str(report)
        assert "TemporaryDirectory" not in blob
        assert "pytest" not in blob
