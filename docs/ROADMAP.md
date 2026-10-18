# ROADMAP.md — Polsia Autonomous Business OS

Entry/exit criteria per phase. Phases 0–1 are implemented; Phase 2 is
complete. Phase 3 is in progress, with slice status recorded below. Later
phases remain architectural targets.

## Phase 0 — Architecture & repository rules ✅ (this pass)
- Layered repo layout with enforced dependency rule (`scripts/check_layering.py`).
- Governance docs: ARCHITECTURE.md (source of truth), ADRs, CONTRIBUTING.md.
- Cross-cutting foundations: typed IDs, error taxonomy, structlog, settings.
- **Exit:** scaffold + shared kernel merged, CI green.

## Phase 1 — Multi-tenant foundation ✅ (this pass)
- User / Organization / Company / OrgMembership with PostgreSQL persistence
  (SQLite fallback for local dev/tests without Docker).
- Auth (register/login/JWT), org membership roles, company CRUD + lifecycle
  state machine, tenancy isolation tests, lifecycle event emission.
- **Exit:** full flow register → org → member → company → status transition
  works via API; isolation tests prove cross-tenant denial; CI green.

## Phase 2 — Autonomous OS core ✅ (complete)
- CompanyState aggregate with versioned snapshots; Goal, KPI, Task engines.
- Agent / Skill / Prompt registries (registration infrastructure only — no
  business specialist agents).
- Durable event bus with subscriptions; MemoryStore port (pgvector adapter);
  ToolGateway port (MCP adapter); PermissionEngine (allow/deny/escalate);
  AgentRuntime port with mock adapter.
- **Delivered:** Goals (state machine) + KPIs (deterministic evaluator);
  Tasks + dependencies (readiness gating) + append-only Executions;
  Agent/Skill/Tool/Model registries (C2 scoping, name@version); Permissions +
  PermissionEngine (deny-by-default) + Approvals; MemoryPort + DB keyword
  adapter; Runtime/Model/Scheduler/Orchestrator ports + MockRuntime +
  capstone kernel-flow test; durable events (C1a: EventStore + events table +
  ADR-0005); UsageRecord + CostCalculator port; Phase 2 isolation-matrix
  security suite.
- **Entry:** Phase 1 merged. **Exit:** all slices implemented, quality gates
  green, core business-agnostic (review-verified).

## Phase 3 — CEO + orchestration engine (in progress)
- **Slice 6 — implemented (commit `37c01d3`):** typed CEO decision and
  append-only tenant-scoped DecisionRecord persistence; bounded internal,
  actor-authorized autonomous decision loop with recorded outcomes and
  permission-checked orchestration. The loop is not exposed as an HTTP route.
- **Slices 7–8 — implemented and validated in the working tree:** Slice 7 has
  disabled-by-default, company-scoped policies; durable delivery claims unique
  on `(policy_id, event_id)`; stable cycle IDs;
  explicit event allowlisting; bounded catch-up and same-envelope replay
  suppression; lineage-based recursion suppression; and SafetyGuard checks ahead
  of execution creation. Delivery claims are now leased, fenced, and bounded
  at-least-once (ADR-0011): a claim is unique on `(policy_id, event_id)`; a
  retryable (`interrupted`) or expired-lease `claimed` row is reclaimed while
  attempts remain, incrementing a monotonic fencing token; `finish` is fenced on
  that token so a stale worker cannot record its outcome; transient faults retry
  up to the bounded budget then become terminal, and deterministic rejections
  are terminal. Recovery is at-least-once with idempotent effects, not
  exactly-once. PermissionEngine remains authoritative: only explicit
  ALLOW proceeds. The app supports lifecycle subscription/catch-up/drain for an
  explicitly injected internal dispatcher. The default production composition
  does not construct a runtime/loop/dispatcher, and no public trigger mutation,
  replay, or execution route is added. Slice 8 adds actor-authorized bounded
  decision history, DecisionRecord-derived cycle summaries, and bounded
  observation summaries through authenticated nested GET-only routes. The
  approved audit follow-ups now also enforce shared permission/approval
  authorization inside every task-attempt creation, cap combined decision JSON
  at 8 KiB of compact UTF-8, validate record company ownership and object-shaped
  outcomes, and preflight plan scope and references before materialization writes.
  SQLite adversarial coverage verifies forged trigger payloads cannot select task,
  actor, or runtime; each configured one-action cap permits only that one action,
  while durable claims suppress redelivery; configurable retry caps reject at the
  configured boundary before retry or execution creation. Lease recovery
  supersedes the ADR-0008 terminal at-most-once decision (see ADR-0011);
  catch-up includes expired leases and excludes terminal rows. Observation task
  reads are bounded: the observer pushes status filters, limits, and the total
  count into the tenant-scoped indexed task query, and the digest is versioned
  to v2 over bounded content plus totals (ADR-0012). Goals/KPIs/agents/approvals
  remain small unbounded reads. The dispatcher remains an explicit internal
  composition dependency; this does not enable autonomous execution in the
  default production app. Full quality gates pass on this working tree
  (467 tests); the test run emitted existing short-JWT-key warnings in test
  settings.
- **Phase exit:** implementation and security evidence cover the bounded loop
  and approved trigger/query slices, tenant isolation holds, and all quality
  gates pass. No business-specific behavior or public execution route is
  introduced.

## Phase 4 — Agent runtime integration
- Pluggable runtime adapters behind the Phase 2/3 `AgentRuntime` port
  (e.g. Hermes adapter once its API is defined).
- Dependency-ordered plan: see `docs/PHASE4-PLAN.md`.
- **Exit:** runtime swap requires zero changes above the port.

## Phase 5 — Generic specialist agent framework
- Registration path for domain specialist agents (capabilities, skills,
  permissions declared declaratively). No specialists shipped in core.
- **Exit:** a sample specialist added as pure configuration + skills.

## Phase 6 — Dashboard / observability
- Separate frontend: company state, goals, KPIs, tasks, events, audit.
- **Exit:** dashboard reads the same API; no business logic duplicated.

## Phase 7 — Business generator
- Bootstrap a company from a plain business idea: generates identity,
  strategy, goals, initial agent roster as **configuration**.
- **Exit:** two generated companies of different types run on identical core.

## Phase 8 — Real business integrations
- Tool adapters for payments, email, web, analytics, etc. via ToolGateway/MCP.

## Phase 9 — Autonomous operation & learning
- Continuous CEO loop, experiment engine, decision records, feedback into
  strategy; human-approval escalations via PermissionEngine.
