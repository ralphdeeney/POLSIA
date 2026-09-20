# ROADMAP.md — Polsia Autonomous Business OS

Entry/exit criteria per phase. Phases 0–1 are **implemented in this pass**;
later phases are architectural targets that Phases 0–1 are shaped to support.

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

## Phase 3 — CEO + orchestration engine
- Generic CEO component: input (state, goals, KPIs, memory, events, agent
  capabilities, permissions) → output (priorities, decisions, tasks,
  delegations, escalations).
- Orchestrator pipeline: objective → task graph → dependency resolution →
  agent selection → dispatch → result collection → verification → state update.
- **Exit:** closed loop runs end-to-end against MockRuntime without an LLM.

## Phase 4 — Agent runtime integration
- Pluggable runtime adapters behind the Phase 2/3 `AgentRuntime` port
  (e.g. Hermes adapter once its API is defined).
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
