"""Advisory decision adapters (Phase 4 mandate §8).

- :class:`DeterministicAdvisoryBaseline` — keyword-scored, abstaining-by-default
  baseline; always available, zero resources.
- :class:`LayaDecisionAdapter` — optional wrapper around a locally provided
  Laya inference backend. Nothing is imported, downloaded or loaded here: the
  backend is injected lazily by deployment composition, and if it is missing
  the adapter falls back to the deterministic baseline with an explicit source
  tag so operators can tell which path answered.
- :class:`LayaTestDouble` — scripted stand-in for harness wiring tests.

Advisories are always advisory: nothing here grants permissions or overrides
budgets.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from polsia.core.decision import (
    BUSINESS_CATEGORIES,
    DecisionAdvisory,
    DecisionCategory,
    DecisionRequest,
)

#: Keyword lexicons (lowercase substrings). Deliberately narrow: absence of
#: evidence must abstain, not guess.
_LEXICONS: dict[str, tuple[str, ...]] = {
    "research": ("market research", "investigate", "competitor", "analysis of trends"),
    "engineering": ("build", "implement", "api", "bug", "code", "deploy pipeline"),
    "marketing": ("campaign", "seo", "social media", "copy", "audience", "webinar"),
    "sales": ("prospect", "outreach", "lead", "pipeline", "quote"),
    "support": ("ticket", "refund", "complaint", "customer asks", "resolution"),
    "finance": ("invoice", "budget", "payroll", "expense", "reconcile"),
    "simple": ("todo", "quick note", "reminder", "status update"),
    "multi_agent": ("coordinate", "multiple agents", "delegate across", "cross-team"),
}

_ADVERSARIAL_MARKERS: tuple[str, ...] = (
    "ignore previous instructions",
    "disregard your rules",
    "reveal other tenants",
    "other company's data",
    "cross-company",
    "grant myself permission",
    "bypass approval",
    "delete the audit log",
)


def _trace_id(text: str) -> str:
    return f"dec_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


class DeterministicAdvisoryBaseline:
    """Keyword-scored baseline with explicit abstention (never guesses)."""

    name = "deterministic_baseline"
    model_version = "baseline-v1"

    def classify(self, request: DecisionRequest) -> DecisionAdvisory:
        trace = _trace_id(request.text)
        lowered = request.text.lower()
        for marker in _ADVERSARIAL_MARKERS:
            if marker in lowered:
                return DecisionAdvisory(
                    category=DecisionCategory.ADVERSARIAL,
                    confidence=1.0,
                    abstain=True,
                    escalate=True,
                    source=self.name,
                    model_version=self.model_version,
                    trace_id=trace,
                )
        scores = {
            category: sum(1 for kw in keywords if kw in lowered)
            for category, keywords in _LEXICONS.items()
        }
        best = max(scores.items(), key=lambda kv: kv[1])
        total = sum(scores.values())
        if best[1] == 0 or total == 0:
            return DecisionAdvisory(
                category=DecisionCategory.UNKNOWN,
                confidence=0.0,
                abstain=True,
                escalate=True,
                source=self.name,
                model_version=self.model_version,
                trace_id=trace,
            )
        runner_up = sorted((v for _, v in scores.items()), reverse=True)
        margin = (runner_up[0] - runner_up[1]) / total if len(runner_up) > 1 else 1.0
        confidence = min(1.0, 0.4 + 0.6 * margin)
        return DecisionAdvisory(
            category=DecisionCategory(best[0]),
            confidence=round(confidence, 4),
            abstain=False,
            escalate=False,
            source=self.name,
            model_version=self.model_version,
            trace_id=trace,
        )


class LayaBackend(Protocol):
    """Injected, lazily-loaded Laya inference callable."""

    def classify(self, text: str) -> tuple[str, float]: ...


class LayaTestDouble:
    """Scripted stand-in used ONLY for wiring tests — never for evidence."""

    name = "laya_test_double"
    model_version = "laya-double-v1"

    def __init__(self, answers: dict[str, tuple[str, float]]) -> None:
        self._answers = dict(answers)

    def classify(self, text: str) -> tuple[str, float]:
        for marker, answer in self._answers.items():
            if marker.lower() in text.lower():
                return answer
        return ("unknown", 0.2)


class LayaDecisionAdapter:
    """Optional Laya wrapper (disabled by default; lazy; fallback wired)."""

    name = "laya"
    model_version = "laya-adapter-v1"

    def __init__(
        self,
        backend: LayaBackend | None = None,
        *,
        baseline: DeterministicAdvisoryBaseline | None = None,
    ) -> None:
        # No import, download or load happens here. Production would inject a
        # backend backed by a separate worker/process, not API startup.
        self._backend = backend
        self._baseline = baseline or DeterministicAdvisoryBaseline()

    @property
    def backend_available(self) -> bool:
        return self._backend is not None

    def classify(self, request: DecisionRequest) -> DecisionAdvisory:
        fallback = self._baseline.classify(request)
        if self._backend is None:
            return DecisionAdvisory(
                category=fallback.category,
                confidence=fallback.confidence,
                abstain=fallback.abstain,
                escalate=fallback.escalate,
                source="baseline_fallback",
                model_version=self.model_version,
                trace_id=fallback.trace_id,
            )
        try:
            raw_category, raw_confidence = self._backend.classify(request.text)
        except Exception:  # noqa: BLE001 — advisory must never break the caller
            return DecisionAdvisory(
                category=DecisionCategory.UNKNOWN,
                confidence=0.0,
                abstain=True,
                escalate=True,
                source="laya_error_fallback",
                model_version=self.model_version,
                trace_id=_trace_id(request.text),
            )
        category = (
            raw_category
            if raw_category in {c.value for c in BUSINESS_CATEGORIES}
            or raw_category == "adversarial"
            else "unknown"
        )
        confidence = min(1.0, max(0.0, float(raw_confidence)))
        low_confidence = confidence < 0.35
        adversarial = category == "adversarial"
        unverifiable = category == "unknown"
        return DecisionAdvisory(
            category=DecisionCategory(category),  # validate the value via the enum
            confidence=round(confidence, 4),
            abstain=low_confidence or unverifiable,
            escalate=low_confidence or adversarial or unverifiable,
            source="laya",
            model_version=self.model_version,
            trace_id=fallback.trace_id,
        )
