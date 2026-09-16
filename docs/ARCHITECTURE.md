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

| Port | Purpose | Phase 1 implementation | Future implementations |
|---|---|---|---|
| Repository ports (`core/tenancy/ports.py`) | Persist aggregates | SQLAlchemy repos | in-memory fakes (tests) |
| `EventBus` (`core/shared/events.py`) | Publish/subscribe domain events | in-process pub/sub | durable bus (Phase 2) |
| `MemoryStore` | write/read/search/update/delete/summarize | — | pgvector, vector DBs (Phase 2) |
| `ToolGateway` | discover/authorize/execute/validate/audit | — | MCP, web, email, … (Phase 2+) |
| `AgentRuntime` | create_session/execute_task/spawn/… | — | Hermes adapter, mocks (Phase 3–4) |
| `PermissionEngine` | allow/deny/escalate on actions | — | policy engine (Phase 2) |

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

## 10. Subagent orchestration policy

See `CONTRIBUTING.md`. Architecture decisions are centralized with the Lead
Architect; subagents execute scoped tasks and never integrate their own work.
