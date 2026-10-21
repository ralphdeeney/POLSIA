"""Deterministic blueprint evaluation (mandate §12).

Every assertion is a deterministic predicate over the typed blueprint and its
evidence provenance. No model judge, no fabricated ground truth. The suite is
also reproducible: two runs of the same labeled dataset must produce the same
report digest (timestamps and latencies excluded).
"""

from __future__ import annotations

from typing import Any

import pytest
from polsia.adapters.business.deterministic import DeterministicBlueprintGenerator
from polsia.eval.business import SCENARIOS, dataset_fingerprint, evaluate_blueprints

REQUIRED_SCENARIOS = {
    "saas_scheduling",
    "ecommerce_specialty_coffee",
    "marketing_agency",
    "ai_automation_service",
    "local_services_cleaning",
    "digital_education_course",
    "marketplace_artisans",
    "subscription_membership",
    "physical_product_hardware",
    "ambiguous_idea",
    "budget_constrained_business",
    "international_customer_base",
    "missing_business_details",
    "contradictory_constraints",
    "prompt_injection_attempt",
}


def test_scenario_dataset_covers_every_required_category() -> None:
    names = {scenario.name for scenario in SCENARIOS}
    assert names == REQUIRED_SCENARIOS
    assert len(SCENARIOS) == 15


def test_every_scenario_passes_deterministic_assertions() -> None:
    report = evaluate_blueprints(DeterministicBlueprintGenerator())
    failures = {
        scenario.scenario: [check.name for check in scenario.failed_checks]
        for scenario in report.scenarios
        if not scenario.passed
    }
    assert not failures, failures
    assert report.passed == report.total == 15
    assert report.pass_rate == 1.0
    assert report.dataset_fingerprint == dataset_fingerprint()


def test_report_is_reproducible() -> None:
    first = evaluate_blueprints()
    second = evaluate_blueprints()
    assert first.report_digest == second.report_digest
    assert first.dataset_fingerprint == second.dataset_fingerprint
    # Blueprint artifacts themselves are deterministic (same per-scenario digest).
    assert [s.blueprint_digest for s in first.scenarios] == [
        s.blueprint_digest for s in second.scenarios
    ]


def test_injection_scenario_never_upgrades_evidence() -> None:
    report = evaluate_blueprints()
    scenario = next(s for s in report.scenarios if s.scenario == "prompt_injection_attempt")
    assert scenario.passed
    checks = {check.name: check.ok for check in scenario.checks}
    assert checks["injection_not_obeyed"] is True
    assert checks["evidence_honest"] is True


def test_budget_and_contradiction_scenarios_are_constrained() -> None:
    report = evaluate_blueprints()
    by_name = {s.scenario: s for s in report.scenarios}
    budget = by_name["budget_constrained_business"]
    assert {c.name for c in budget.checks if c.ok} >= {
        "budget_constraint_respected",
        "budget_risk_surfaced",
    }
    contradiction = by_name["contradictory_constraints"]
    assert {c.name for c in contradiction.checks if c.ok} >= {
        "contradiction_surfaced",
        "budget_constraint_respected",
    }


def test_non_saas_business_models_are_inferred() -> None:
    report = evaluate_blueprints()
    models = {s.scenario: s.business_model for s in report.scenarios}
    assert models["ecommerce_specialty_coffee"] == "ecommerce"
    assert models["marketing_agency"] == "agency"
    assert models["marketplace_artisans"] == "marketplace"
    assert models["local_services_cleaning"] == "local_services"
    assert models["digital_education_course"] == "digital_education"
    assert models["physical_product_hardware"] == "physical_product"
    assert models["subscription_membership"] == "subscription"


def test_model_backed_arm_is_reported_not_run() -> None:
    report = evaluate_blueprints()
    assert report.model_arm.startswith("NOT RUN")
    assert any("no credentials" in note or "offline" in note for note in report.notes)


class _FailingGenerator:
    """Every call raises, like a provider outage or read timeout."""

    name = "failing-live"

    async def generate(self, idea: Any, *, context: Any = None) -> Any:
        raise TimeoutError("provider timed out (simulated)")


def test_generator_failure_is_recorded_per_scenario_not_raised() -> None:
    report = evaluate_blueprints(_FailingGenerator())
    assert report.total == 15
    assert report.passed == 0
    assert all(result.passed is False for result in report.scenarios)
    assert all(result.error and "TimeoutError" in result.error for result in report.scenarios)
    assert all(
        [check.name for check in result.failed_checks] == ["generator_error"]
        for result in report.scenarios
    )
    # The report is still produced, so a dead provider yields evidence, not a crash.
    assert report.report_digest


def test_generator_factory_builds_one_generator_per_scenario() -> None:
    created: list[DeterministicBlueprintGenerator] = []

    def factory() -> DeterministicBlueprintGenerator:
        generator = DeterministicBlueprintGenerator()
        created.append(generator)
        return generator

    report = evaluate_blueprints(generator_factory=factory, scenarios=SCENARIOS[:3])
    assert len(created) == 3
    assert report.total == 3
    assert report.passed == 3
    assert report.generator == "factory"


@pytest.mark.parametrize(
    "scenario",
    [s for s in SCENARIOS if s.expect_injection_contained or s.category == "adversarial"],
    ids=lambda s: s.name,
)
def test_adversarial_scenario_checks_are_explicit(scenario) -> None:
    assert scenario.expect_injection_contained is True
