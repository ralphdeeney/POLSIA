# ARCHITECTURE.md — Polsia Autonomous Business OS

This document is the **single source of truth** for the Polsia architecture.
Every architectural change must be reflected here before it is merged.

## 1. What Polsia is

Polsia is a **business-agnostic operating system**: a platform on which
arbitrary businesses (SaaS, e-commerce, agency, marketplace, local business,
info-products, …) can be created as **configuration/state instances** and then
operated autonomously.

**Rule zero — business agnosticism:** nothing in `polsia.core` may depend on
an industry, product type, customer type, or business model. All
business-specific data lives in schema-flex fields (`identity`, `strategy`,
`configuration` JSON) owned by the company instance, never in code.

## 2. Core loop (target architecture)

```
User → Organization → Company → Company State → CEO → Goals/KPIs
  → Task System → Agent Orchestrator → Specialist Agents
  → Skills → Tools → Memory → Events → Results
  → Company State → CEO   (autonomous loop)
```

## 3. Layered module layout

```
polsia/
├── core/        Domain entities, value objects, invariants, PORTS (interfaces).
│                Zero I/O dependencies. Imports nothing above it.        [innermost]
├── adapters/    Implementations of core ports: persistence (SQLAlchemy),
│                events (in-process bus now, durable bus later),
│                runtime (agent runtime adapters, Phase 3+).
├── services/    Application/use-case layer: orchestrates core + adapters,
│                owns transactions and event emission.
├── api/         FastAPI routers, request/response schemas, auth, tenant context.
│                Domain entities never leak; only Pydantic DTOs.
└── infra/       Wiring: settings, logging, DB session, DI containers, seed.
```

**Dependency rule (enforced by `scripts/check_layering.py` in CI):**

- `core` must not import `api`, `adapters`, `services`, or `infra`.
- `adapters` and `services` depend on `core` via ports (interfaces/protocols).
- `api` and `infra` may import anything; `infra` is the only place that
  constructs concrete adapters and injects them.

## 4. Key abstractions (ports)

| Port | Purpose | Current implementation | Future implementations |
|---|---|---|---|
| Repository ports (`core/tenancy`, `core/planning`, `core/tasks`, `core/registry`, `core/permissions`, `core/cost`) | Persist aggregates | SQLAlchemy repos | in-memory fakes (tests) |
| `EventBus` (`core/shared/events.py`) | Publish/subscribe domain events (sync, ADR-0005) | in-process pub/sub | durable/outbox bus (later) |
| `EventStore` (`core/shared/events.py`) | Durable event persistence (ADR-0005) | `SqlAlchemyEventStore` (events table) | outbox dispatcher |
| `MemoryPort` (`core/memory/ports.py`) | store/retrieve/search/delete/list, company-scoped | DB-backed keyword store | pgvector, OpenViking |
| `RuntimePort` (`core/runtime/ports.py`) | execute an agent/skill task | `MockRuntime` | custom/hosted runtimes |
| `ModelPort` (`core/runtime/ports.py`) | generate/stream, provider-neutral | `NullModelProvider` | any vendor SDK adapter |
| `SchedulerPort` (`core/scheduling/__init__.py`) | immediate/at/periodic/after-event/condition | `StubScheduler` | cron/Celery/Temporal worker |
| `OrchestratorPort` (`core/scheduling/__init__.py`) | goal → task decomposition | `DeterministicOrchestrator` | Phase 3 CEO planner |
| `DecisionRecordRepository` (`core/ceo/records.py`) | append and tenant-scoped read of validated CEO decisions | SQLAlchemy `decision_records` table | — |
| `CostCalculator` (`core/cost/ports.py`) | estimate execution cost | `NullCostCalculator` | pricing adapters |
| `PermissionEngine` (`services/permissions.py`) | allow/deny/approval_required, deny-by-default | policy engine (Phase 2) | external policy engines |

## 5. Identity & tenancy model

```
User (usr_…)  ── many-to-many (role: owner|admin|member) ──  Organization (org_…)
Organization  ── 1:N ──  Company (cmp_…)
```

- IDs are **prefixed ULIDs** (`usr_`, `org_`, `cmp_`): sortable, greppable, typed at the domain layer (`UserId`, `OrganizationId`, `CompanyId` newtype wrappers).
- Every company is owned by exactly one organization. All company access is
  scoped by organization; the repository layer receives the tenant scope as an
  explicit parameter — there is no implicit global tenant.
- Authorization: a user may act on an organization only through an
  `OrgMembership` (owner > admin > member).

## 5b. Phase 2 — OS core primitives (company-scoped)

```
Company ─┬─ Goals (state machine; optional parent) ── KPIs (direction: increase/decrease/maintain;
         │   deterministic evaluator → not_started/on_track/at_risk/achieved/missed)
         ├─ Tasks (state machine; explicit dependencies gate pending→ready) ── Executions (append-only attempts)
         ├─ Agents (company-scoped configuration; free-form type)
         ├─ Permissions (allow/deny/approval_required; deny-by-default) ── Approvals (human-in-the-loop)
         ├─ Memory records (company-scoped, keyword-search port)
         └─ Usage records (per-execution, provider-neutral cost abstraction)
Platform-global registries: Skills, Tools, Models (versioned, unique name@version).
Global availability ≠ authorization — a registered capability implies no usage right
(the PermissionEngine gates agent → tool/action at runtime).
```

State machines live in the domain (`GOAL_STATUS_TRANSITIONS`,
`TASK_STATUS_TRANSITIONS`, `EXECUTION_STATUS_TRANSITIONS`) and are the only
transition authority. Durable events: every Phase 2 mutation persists its
envelope to the `events` table (ADR-0005) *before* dispatching on the bus.

## 6. Company model (business-agnostic)

`Company` carries: `name`, `slug`, `identity` (JSON), `mission` (text),
`strategy` (JSON), `configuration` (JSON), `status`
(`draft → active ⇄ paused → archived`).

The OS imposes **no schema** on identity/strategy/configuration — those belong
to the company instance (the future business). The company lifecycle is an
explicit state machine; illegal transitions raise `InvalidStatusTransition`.

## 7. Error taxonomy

`PolsiaError` (base) → domain errors (`InvalidSlug`, `InvalidStatusTransition`,
`DuplicateEmail`, `NotAMember`, …) → API layer maps them to RFC 9457
problem-details responses. Adapters raise adapter errors and translate them at
the boundary.

## 8. Events

All state changes that other components may care about are emitted as
`EventEnvelope`s: `{id, type, occurred_at, tenant (org/company), payload}`.
Event types are namespaced snake tuples (`company.created`,
`company.status_changed`, `user.registered`, …). Phase 1 ships an in-process
bus with a logging subscriber; the envelope contract is stable so a durable
bus can replace the adapter without touching services.

## 9. Observability

- `structlog` JSON logs with request ID / trace ID bound per request.
- `/healthz` (liveness) and `/readyz` (DB connectivity) endpoints.
- Coverage, lint, typecheck, and layer-boundary gates run in CI.

## 10. Durable events (ADR-0005)

All Phase 2 mutations emit `EventEnvelope`s that are persisted to the
`events` table via the `EventStore` port **first** (`emit()` helper: mutation
→ persist → dispatch), then dispatched on the synchronous in-process bus.
`append` participates in the caller's transaction: a rolled-back mutation
leaves no event behind. Phase 0–1 (tenancy) events are dispatched but not yet
durably persisted (documented debt).

## 11. Phase 3 — bounded CEO loop and event-trigger boundary

Phase 3 is being delivered in slices; see `ROADMAP.md` for implementation
status. The current committed Slice 6 implementation provides typed CEO
decisions and append-only, organization/company-scoped `DecisionRecord`s in the
`decision_records` table (ADR-0007). Records include the decision payload and type,
rationale/confidence/model reference, cycle ID, observation digest, outcome
context, and timestamps. The combined payload and outcome context are limited to
8 KiB of compact UTF-8 JSON at both domain and response DTO boundaries. The
internal autonomous loop is finite and actor-authorized, records decisions and
outcomes, and uses the shared execution-authorization boundary before execution.
`ExecutionService.start_execution` requires and invokes that same boundary before
any task transition or attempt write; no direct service caller can bypass it.
The public task-start endpoint remains an attempt-record API and does not invoke a
runtime. Only an explicit permission `ALLOW` proceeds; deny and approval-required
outcomes create no attempt, and approvals are never auto-approved.

The approved Slice 7 trigger design is internal. The current working tree has
validated `SafetyLimits`, `EventTriggerPolicy`, and `SafetyGuard` contracts:
policies are disabled by default, match only the explicit `task.ready` /
`task.failed` allowlist, and limits cover actions, tasks, executions, retries,
event age, and future clock skew. `SafetyGuard` checks tenant/company scope,
active company and policy, event type and age, decision/task binding and
readiness, and cycle counters. PermissionEngine / orchestration authorization
remains authoritative: only an explicit `ALLOW` may proceed; deny and
approval-required outcomes stop dispatch, and approvals are never
auto-approved.

Slice 7 persists a unique `(policy_id, event_id)` row and a stable cycle ID,
suppresses same-envelope redelivery, and records recursive lineage when
suppressing descendant events. Delivery rows are leased, fenced, and bounded
at-least-once (ADR-0011): `claim` inserts the unique row or takes over a
retryable (`interrupted`) or expired-lease `claimed` row while attempts remain,
incrementing a monotonic `fencing_token`; `finish` is fenced on that token so a
stale worker cannot overwrite the newer owner's outcome. Transient faults are
marked `interrupted` (reclaimable) until the attempt budget is spent, then
`failed`; deterministic rejections are terminal. Catch-up selects bounded
eligible durable envelopes, including expired leases, and excludes terminal rows
so they never consume the catch-up budget. Recovery targets at-least-once
processing with idempotent business effects, not exactly-once: an expired lease
can permit a concurrent duplicate cycle, the fenced worker only cannot record
it, and the task state machine plus the stable cycle ID bound the effect.
Dispatcher lifecycle subscription/catch-up/drain is enabled only for an
explicitly injected internal dispatcher; the default production composition does
not construct a runtime/loop/dispatcher. SQLite-backed adversarial tests cover
forged task/actor/runtime payload fields, one-action caps, retry-cap rejection
before attempt creation, lease expiry, stale-worker fencing, bounded retry, and
cross-company dispatch isolation. Plan materialization validates the complete
plan, company and goal scope, and all dependency references before its first
task write. Event payloads are untrusted and do not select tasks or grant
permissions. There is no public replay operation or trigger-policy mutation API.
Cycle summaries are derived from persisted DecisionRecords; see ADR-0008,
ADR-0009, and ADR-0011.

The Slice 8 query boundary is authenticated, nested organization/company `GET`
only: bounded decision history, cycle summaries, and observation summary. Its
actor-authorized query service and mounted DTO routes authorize membership and
company ownership before reading. Observation response lists are capped while
count fields, including `kpis_total`, report uncapped totals. The observer now
pushes task status filters, limits, and the total count into the tenant-scoped,
indexed task query, so observation task reads are bounded by the configured
limits plus one count query rather than the task-table size
(`TaskRepository.list_for_company(status=..., limit=...)` and
`count_for_company`). The observation digest is versioned to v2: it hashes the
bounded observation content plus totals instead of every task row (ADR-0012).
Goals, KPIs, agents, and approvals remain unbounded but are small by design. The
prior "reads and digest are unbounded" limitation is therefore resolved for
tasks; no public business-specific route, trigger mutation route, or replay
route is part of the boundary. See `ROADMAP.md` for exact slice and validation
status.

### 11b. Phase 4 — native agent runtime (bounded, provider-neutral)

Phase 4 added the first real executor behind the `RuntimePort`/`ModelPort`
contracts while preserving every Phase 2/3 boundary:
`NativeAgentRuntime` (`polsia/adapters/agent_runtime/`) is an explicit state
machine with hard budgets (iterations, elapsed time, cumulative tokens, cost,
tool calls), a verifiable-completion contract (a model claiming completion
without evidence fails the run), sanitized traces and a deterministic
idempotency/trace identity (ADR-0013). Tool calls execute only through the
strict `ToolGateway`: read-only capabilities only, unsafe capability families
structurally unreachable, the resolved `tool_permissions` decision set honored
when present, and the trusted company/task context never derived from model
output.

The first real model adapter is `OpenAICompatibleModelProvider`
(`polsia/adapters/models/`, ADR-0014): configurable base URL/key env/model,
bounded retries with backoff, normalized errors, conservative capability
detection, and honest token/cost accounting (nothing is fabricated when a
provider omits usage). `NullModelProvider`, `ScriptedModelProvider` and the
Phase 3 `MockRuntime` remain the test/baseline paths.

Prompt construction is versioned and bounded (`prompt_composer.py` +
`polsia/adapters/runtime/prompts/*.md`), reusing the existing Skills Registry
and tenant-scoped memory as input sections. An optional, advisory-only
decision layer (`polsia/core/decision/`, `polsia/adapters/decision/`,
ADR-0015) supports deterministic routing today and a lazily injected Laya
backend later; advisories can never grant permissions or override budgets.
Evaluation harnesses live in `polsia/eval/` with the runnable entry point
`scripts/run_agent_evaluations.py`. Production autonomous composition remains
unwired: the runtime is injected explicitly, exactly as in Phase 3.

### 11c. Phase 4 wave 3 — business creation workflow

The front of the operating system is now wired end to end:

- **Intake** (`polsia/services/business_intake.py`, router
  `/organizations/{org_id}/business-ideas`): owner-authenticated,
  tenant-scoped submission with the exact user text preserved, validated
  lifecycle status, multi-idea support and request idempotency keys.
- **Blueprint** (`polsia/core/business/`, `polsia/services/blueprint_service.py`):
  a typed, validated artifact covering identity, problem/customer/value,
  product, revenue/pricing hypotheses, market/competition, go-to-market,
  MVP, operations, technology, financial assumptions, goals, KPIs, agent
  roster, risks, owner actions and validation requirements. Every claim
  carries an explicit evidence status. Generation is a `BlueprintPort`:
  deterministic offline by default, model-backed through the existing
  `ModelPort` with versioned prompts and hard token/time/call budgets
  (ADR-0016).
- **Bootstrap** (`polsia/services/bootstrap.py`): materializes a validated
  blueprint through the existing domain services into a company, goals, KPIs,
  company-scoped agents (including the CEO), initial memory and
  planning-readiness state. Idempotent by `bootstrap:{org}:{idea}`; refuses
  unvalidated blueprints; never spends, registers or activates anything.
- **CEO initialization** (`polsia/services/ceo_init.py`): configures the CEO
  from trusted state and produces the initial plan through the deterministic
  `PlanningEngine`; tasks are materialized only after plan validation.
- **Verification** (`polsia/services/verification.py`): artifact completion
  (schema, sections, consistency, ownership, persistence, provenance) is
  verified separately from external-action evidence and research sourcing.
- **Execution**: the first work item runs through
  `OrchestrationService.execute_task` and `NativeAgentRuntime` under the
  unchanged permission gate; the result is verified and observed through the
  Phase 2/3 observation boundary.

Blueprint quality is scored by a deterministic, reproducible scenario suite
(`polsia/eval/business.py`, `scripts/run_business_evaluations.py`); the E2E
suite (`tests/integration/test_business_creation_e2e.py`) covers the 16-step
positive flow plus isolation, outage, budget, rollback/retry, forged-id and
prompt-injection negatives. Model-backed arms remain scripted-only; live
provider testing is **NOT RUN** without credentials.

## 12. Subagent orchestration policy

See `CONTRIBUTING.md`. Architecture decisions are centralized with the Lead
Architect; subagents execute scoped tasks and never integrate their own work.
