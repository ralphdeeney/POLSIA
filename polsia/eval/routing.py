"""Routing A/B evaluation runner (mandate §10).

Runs the SAME labeled dataset and scoring criteria through:

- A: the deterministic baseline classifier (always available);
- B: a Laya-assisted adapter when a locally available Laya backend is injected
  (real-model evaluation stays NOT RUN in this milestone);
- C: an LLM-assisted classifier when an evaluation provider is configured.

The runner never lets Laya (or any classifier) make authoritative decisions:
advisories are recorded for comparison only.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from polsia.adapters.decision import (
    DeterministicAdvisoryBaseline,
    LayaDecisionAdapter,
)
from polsia.eval import RoutingMetrics, dataset, evaluate_classifier

AdvisoryFactory = Callable[[], Any]


@dataclasses.dataclass(frozen=True)
class RoutingArmResult:
    """One arm of the routing comparison."""

    arm: str
    source: str
    metrics: RoutingMetrics
    items: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "source": self.source,
            "total": self.metrics.total,
            "accuracy": self.metrics.accuracy,
            "accuracy_over_decisive": self.metrics.accuracy_over_decisive,
            "abstention_rate": self.metrics.abstention_rate,
            "escalation_rate": self.metrics.escalation_rate,
            "unsafe_route_rate": self.metrics.unsafe_route_rate,
            "per_class_precision": self.metrics.per_class_precision,
            "per_class_recall": self.metrics.per_class_recall,
            "confusion": self.metrics.confusion,
            "avg_latency_ms": self.metrics.avg_latency_ms,
            "items": self.items,
        }


def run_deterministic_arm() -> RoutingArmResult:
    metrics, items = evaluate_classifier(DeterministicAdvisoryBaseline(), dataset.ROUTE_EXAMPLES)
    return RoutingArmResult(arm="A", source="deterministic_baseline", metrics=metrics, items=items)


def run_laya_arm(backend: Any | None) -> RoutingArmResult | None:
    """Laya-assisted arm; ``None`` when no backend is available (NOT RUN)."""
    adapter = LayaDecisionAdapter(backend)
    if not adapter.backend_available:
        return None
    metrics, items = evaluate_classifier(adapter, dataset.ROUTE_EXAMPLES)
    return RoutingArmResult(arm="B", source="laya", metrics=metrics, items=items)


def run_llm_arm(classifier: Any | None) -> RoutingArmResult | None:
    """LLM-assisted arm wired only when an evaluation provider is configured."""
    if classifier is None:
        return None
    metrics, items = evaluate_classifier(classifier, dataset.ROUTE_EXAMPLES)
    return RoutingArmResult(arm="C", source=classifier.name, metrics=metrics, items=items)


@dataclasses.dataclass(frozen=True)
class RoutingComparisonReport:
    fingerprint: str
    arms: list[RoutingArmResult]
    laya_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_fingerprint": self.fingerprint,
            "laya_status": self.laya_status,
            "arms": [arm.to_dict() for arm in self.arms],
            "recommended_default": self.recommended_default,
        }

    @property
    def recommended_default(self) -> str:
        """Evidence-based recommendation: never guess Laya's value."""
        best = min(
            (arm for arm in self.arms if arm.arm == "A" or arm.source == "laya"),
            key=lambda arm: (-arm.metrics.accuracy, arm.metrics.unsafe_route_rate),
            default=None,
        )
        if best is None:
            return "deterministic_baseline"
        if best.source == "laya":
            return "laya_assisted"
        return "deterministic_baseline"


def run_routing_comparison(
    laya_backend: Any | None = None, llm_classifier: Any | None = None
) -> RoutingComparisonReport:
    arms: list[RoutingArmResult] = [run_deterministic_arm()]
    laya_arm = run_laya_arm(laya_backend)
    laya_status = "RUN" if laya_arm is not None else "NOT RUN (no local Laya backend)"
    if laya_arm is not None:
        arms.append(laya_arm)
    llm_arm = run_llm_arm(llm_classifier)
    if llm_arm is not None:
        arms.append(llm_arm)
    return RoutingComparisonReport(
        fingerprint=dataset.ROUTE_DATASET_FINGERPRINT,
        arms=arms,
        laya_status=laya_status,
    )
