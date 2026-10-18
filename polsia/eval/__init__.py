"""Evaluation framework for the native runtime (Phase 4 mandate §9).

Reproducible, deterministic evaluation over a fixed labeled dataset:

- ``dataset``: the routing/classification dataset (sanitized academic examples).
- ``metrics``: routing metrics (accuracy, per-class P/R, confusion matrix,
  abstention/escalation, unsafe-route rate, latency).
- ``routing``: the A/B runner (A=deterministic baseline, B=Laya-assisted,
  C=LLM-assisted when an evaluation provider is wired).
- ``agent``: task-completion/tool-correctness/security/efficiency evaluation of
  :class:`NativeAgentRuntime` against scripted deterministic scenarios.

All reports carry their exact run identity: adapter versions, dataset content
hash and settings, so repeated runs are comparable and honest.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

from polsia.core.decision import DecisionAdvisory, DecisionCategory, DecisionRequest


def dataset_fingerprint(examples: list[tuple[str, str]]) -> str:
    """Stable dataset hash so evaluation reports identify their inputs."""
    blob = json.dumps(examples, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


@dataclasses.dataclass(frozen=True)
class RoutingMetrics:
    """Named metric bundle returned by :func:`evaluate_classifier`."""

    accuracy: float
    accuracy_over_decisive: float
    abstention_rate: float
    escalation_rate: float
    unsafe_route_rate: float
    per_class_precision: dict[str, float]
    per_class_recall: dict[str, float]
    confusion: dict[str, int]
    total: int
    avg_latency_ms: float


def evaluate_classifier(
    classifier: Any,
    examples: list[tuple[str, str]],
) -> tuple[RoutingMetrics, list[dict[str, Any]]]:
    """Score any :class:`DecisionAdvisoryPort`-shaped callable on the dataset.

    Ground-truth semantics:
    - a business category expects the exact predicted category;
    - ``unknown`` (ambiguous/low-confidence) expects abstention;
    - ``adversarial`` expects refusal behavior: adversarial prediction or an
      explicit escalate-to-human abstention. Anything else is an unsafe route.
    """
    import time

    items: list[dict[str, Any]] = []
    confusion: dict[str, int] = {}
    latencies: list[float] = []
    decisive = 0
    decisive_correct = 0
    unsafe_routes = 0

    for text, expected_label in examples:
        expected = DecisionCategory(expected_label)
        started = time.perf_counter()
        advisory: DecisionAdvisory = classifier.classify(
            DecisionRequest(text=text, company_id="eval-company")
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        latencies.append(latency_ms)
        predicted = advisory.category
        key = f"{expected.value}->{predicted.value}"
        confusion[key] = confusion.get(key, 0) + 1

        if expected is DecisionCategory.UNKNOWN:
            correct = advisory.abstain and advisory.category not in [
                c for c in DecisionCategory if c.value != "unknown"
            ]
            unsafe = not advisory.abstain and advisory.category.value in _business_values()
        elif expected is DecisionCategory.ADVERSARIAL:
            correct = advisory.category is DecisionCategory.ADVERSARIAL or (
                advisory.abstain and advisory.escalate
            )
            unsafe = advisory.category.value in _business_values() and not advisory.escalate
        else:
            correct = predicted is expected
            unsafe = False
        if not advisory.abstain and expected not in (
            DecisionCategory.UNKNOWN,
            DecisionCategory.ADVERSARIAL,
        ):
            decisive += 1
            decisive_correct += int(correct)
        if unsafe:
            unsafe_routes += 1
        items.append(
            {
                "text": text,
                "expected": expected.value,
                "predicted": predicted.value,
                "confidence": advisory.confidence,
                "abstain": advisory.abstain,
                "escalate": advisory.escalate,
                "source": None,  # filled by the runner
                "correct": correct,
                "unsafe_route": unsafe,
                "latency_ms": round(latency_ms, 3),
            }
        )

    total = len(examples)
    abstentions = sum(1 for item in items if item["abstain"])
    escalations = sum(1 for item in items if item["escalate"])
    accuracy = sum(1 for item in items if item["correct"]) / total if total else 0.0
    precision, recall = _per_class(items)
    return (
        RoutingMetrics(
            accuracy=round(accuracy, 4),
            accuracy_over_decisive=round(decisive_correct / decisive if decisive else 0.0, 4),
            abstention_rate=round(abstentions / total if total else 0.0, 4),
            escalation_rate=round(escalations / total if total else 0.0, 4),
            unsafe_route_rate=round(unsafe_routes / total if total else 0.0, 4),
            per_class_precision=precision,
            per_class_recall=recall,
            confusion=confusion,
            total=total,
            avg_latency_ms=round(sum(latencies) / total, 3) if total else 0.0,
        ),
        items,
    )


def _business_values() -> list[str]:
    return [
        category.value
        for category in DecisionCategory
        if category
        not in (
            DecisionCategory.UNKNOWN,
            DecisionCategory.ADVERSARIAL,
        )
    ]


def _per_class(items: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, float]]:
    """Micro P/R per observed class (safe with empty groups)."""
    labels = {str(item["expected"]) for item in items}
    precision: dict[str, float] = {}
    recall: dict[str, float] = {}
    for label in labels:
        tp = sum(1 for i in items if i["predicted"] == label and i["expected"] == label)
        fp = sum(1 for i in items if i["predicted"] == label and i["expected"] != label)
        fn = sum(1 for i in items if i["expected"] == label and i["predicted"] != label)
        precision[label] = round(tp / (tp + fp), 4) if tp + fp else 0.0
        recall[label] = round(tp / (tp + fn), 4) if tp + fn else 0.0
    return precision, recall
