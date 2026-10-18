# Phase 4 implementation plan (dependency-ordered)

Status: planning document. No Phase 4 code is written during the Phase 3
completion pass. This plan is grounded in the modules that already exist so it
does not re-implement Phase 2/3 work.

## What already exists (do not rebuild)

| Capability (assignment §10) | Where it lives today | State |
| --- | --- | --- |
| Agent Registry | `polsia/services/registries.py`, `registry_repositories.py` (company-scoped `name@version`) | implemented |
| Skills Registry | `registry_repositories.py` (platform-global `name@version`) | implemented |
| Task Dispatcher | `polsia/services/orchestration.py`, `tasks.py` (dependency gating, retries) | implemented |
| Company-scoped Memory | `polsia/services/memory.py`, `memory_repositories.py` (MemoryPort) | implemented |
| Business KPI Observation | `polsia/services/observation.py`, ADR-0010/ADR-0012 | implemented (bounded task reads) |
| Event-driven Autonomous Cycles | `polsia/services/event_triggers.py`, `autonomous_loop.py`, ADR-0008/ADR-0011 | implemented (leased recovery) |
| Autonomous CEO planning | `polsia/services/planning_engine.py` (deterministic `PlanEnginePort`) | implemented, deterministic only |
| Durable runtime/model/scheduler ports | `polsia/core/runtime/ports.py`, `polsia/core/scheduling/`, `adapters/runtime/` | ports exist; only `MockRuntime`/`NullModelProvider`/`StubScheduler` |
| Provider-neutral execution + safety | `orchestration.py`, `execution_authorization.py`, `core/ceo/triggers.py` (SafetyGuard) | implemented |

Not yet implemented: Hermes (or any real) runtime adapter, the production
runtime composition, business-idea intake, blueprint generation, and company
bootstrap.

## Dependency-ordered waves

### Wave 1 — Runtime adapter seam hardening (prerequisite for everything)
1. **Contract tests for `RuntimePort`/`ModelPort`/`SchedulerPort`.** Add a shared
   conformance suite every adapter must pass (`tests/adapters/runtime_contract`)
   so a new adapter cannot silently diverge. Depends on: nothing.
2. **HTTP/LLM transport abstraction.** A thin internal client seam (timeouts,
   retries, rate limits, redacted logging) used by real adapters; no vendor SDK
   leaks above the port. Depends on 1.
3. **Secret handling.** Adapter credentials resolved from `Settings`/env only,
   never persisted, never logged. Depends on 2.

### Wave 2 — Hermes runtime adapter (Phase 4 headline)
4. **Hermes adapter implementing `RuntimePort` + `ModelPort`** behind the
   existing ports, with tool-call records, token usage, and failure mapping into
   `RuntimeResult.failed`. Depends on Wave 1.
5. **Async tool-call dispatch through `ToolGateway`/permission checks.** Tool
   authorization must reuse the existing `PermissionEngine`; no new bypass.
   Depends on 4.
6. **Adapter-isolation security tests** (untrusted model output cannot select
   tasks, agents, permissions, or another tenant's data). Depends on 4-5.
7. **Optional production composition toggle** that wires `RuntimePort` without
   enabling autonomous execution automatically. Depends on 4.

### Wave 3 — Business idea intake and blueprint (Phase 7 pulled forward)
8. **`BusinessIdea` domain entity + intake port** (idea text, constraints,
   owner, target market). Depends on: nothing (tenancy primitives exist).
9. **`CompanyBlueprint` domain type** (identity, strategy, goals, initial agent
   roster, KPI set) as structured data, validated like `Plan` (bounded JSON,
   scope checks). Depends on 8.
10. **Blueprint generator behind a `BlueprintPort`** (deterministic default
    first, model-backed later via Wave 1-2). Depends on 9; benefits from 4.
11. **Company bootstrap service** that materializes a blueprint into real
    company + goals + KPIs + agent registry entries through existing services
    (no direct repository writes). Depends on 9-10.
12. **End-to-end test**: idea -> blueprint -> company with independent state,
    running the Phase 3 loop untouched. Depends on 11.

### Wave 4 — Production autonomous composition (explicit, opt-in)
13. **Composition root** wiring observer + prioritizer + delegation +
    authorization + orchestration + runtime + dispatcher + decision records for
    one company, with bounded `SafetyLimits` and `ObservationLimits`. Depends on
    Wave 2 (real runtime) and Phase 3 primitives.
14. **Scheduler/worker entrypoint** subscribing the dispatcher to the durable
    event store with catch-up + drain, using the leased/fenced claims (ADR-0011).
    Depends on 13.
15. **Operational guarantees**: idempotent effects keyed by trigger cycle ID,
    Postgres row-lock validation of claim takeover, metrics for
    attempts/leases/terminal states. Depends on 14.
16. **Explicit kill switch + approval escalation** surfaced through the existing
    approval model. Depends on 15.

### Wave 5 — Planning intelligence
17. **Model-backed `PlanEnginePort` implementation** that emits the same
    validated `Plan` structure (keep the deterministic engine as default/fallback).
    Depends on Wave 1-2.
18. **Verification/KPI linkage** from plan `VerificationCriteria` to observations
    and decision records. Depends on 17, uses Phase 3 observation.

## Recommended immediate milestone

**Wave 1 + item 4 (Hermes adapter behind `RuntimePort`)**. It is the smallest
increment that unblocks every later wave, is entirely behind existing ports, and
needs no schema change. Exit: the runtime-contract suite passes for both
`MockRuntime` and the Hermes adapter, and swapping the runtime requires no
change above the port.

## Guardrails carried forward from Phase 3

- No public trigger-mutation or replay route.
- No execution without an explicit permission `ALLOW`; approvals never
  auto-approved.
- Tenant/company scope enforced in application code and persistence boundaries,
  not prompts.
- Bounded loops, bounded observation reads, and the 8 KiB decision-record JSON
  ceiling remain in force.
