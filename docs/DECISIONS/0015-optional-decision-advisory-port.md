# ADR-0015: Optional decision-advisory port (Laya) with deterministic baseline

Status: accepted (Phase 4)

## Context

Phase 4 §8 asked for an evaluation of Laya (Convai Innovations' typed-decision
model) as an optional routing/classification layer — without letting it become
authoritative and without loading it during ordinary API startup.

## Decision

- New optional core port `DecisionAdvisoryPort` (`polsia/core/decision/`)
  returning a `DecisionAdvisory` (category, confidence, abstain, escalate,
  source, model_version, trace id). Invariant enforcement in the dataclass:
  confidence bounded to [0,1], abstention always escalates, adversarial
  detections always escalate.
- `DeterministicAdvisoryBaseline` (`polsia/adapters/decision/`) is the default:
  keyword-lexicon scoring with explicit abstention — absence of evidence is
  `UNKNOWN + abstain + escalate`, never a guess; adversarial/cross-tenant
  markers are always escalated or refused.
- `LayaDecisionAdapter` wraps an injected, lazily-provided inference backend.
  Nothing is imported, downloaded or loaded at import time or API startup; if
  the backend is missing, the adapter returns the baseline outcome with the
  explicit `baseline_fallback` source tag; backend crashes yield an escalating
  `laya_error_fallback` advisory. Unknown categories from a backend always
  abstain/escalate; low-confidence output abstains.
- Advisories are advisory: no code path lets a DecisionAdvisory grant
  permissions, approve payments, bridge tenants, override budgets or execute
  actions. The only consumers in this milestone are evaluation and observation
  plumbing.

## Resource assessment (honest)

Laya real-model evaluation is NOT RUN: no local checkpoint, no benchmark of
inference latency/peak RSS/initialization time was possible in this
environment, and none is assumed. The adapter, test double, dataset and A/B
harness exist so a real evaluation is a configuration change, not a rewrite
(`polsia/eval/routing.py`). The default routing strategy remains the
deterministic baseline until measured evidence says otherwise (the comparison
report recommends a strategy only from measured arms).

## Verification

- Advisory unit tests (`tests/adapters/test_decision_advisory.py`).
- Routing A/B evaluation on the fixed labeled dataset: deterministic baseline
  accuracy 1.0, unsafe-route rate 0.0, abstention on ambiguous/adversarial
  inputs 100% (`polsia/eval/dataset.py`, `tests/eval/test_evaluations.py`).
  Laya arm: NOT RUN; LLM arm: NOT RUN (no evaluation provider configured).
