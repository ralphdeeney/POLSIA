# Polsia — Autonomous Business Operating System

Polsia is a **business-agnostic platform**: the operating-system
infrastructure on which arbitrary businesses (SaaS, e-commerce, agency,
marketplace, …) can later be created as configuration/state instances and
operated autonomously. Nothing in the core depends on a specific industry,
product, or business model.

```
User → Organization → Company → Company State → CEO → Goals/KPIs
  → Task System → Agent Orchestrator → Specialist Agents
  → Skills → Tools → Memory → Events → Results → Company State → CEO
```

- **Architecture** (single source of truth): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Roadmap** (Phases 0–9): [docs/ROADMAP.md](docs/ROADMAP.md)
- **Decision records**: [docs/DECISIONS/](docs/DECISIONS/)
- **Contribution & subagent policy**: [CONTRIBUTING.md](CONTRIBUTING.md)

**Status:** Phases 0–2 complete — layered architecture, shared kernel,
multi-tenant foundation (User / Organization / Company), JWT auth, company
lifecycle state machine, durable events (events table + EventStore port), and
the Phase 2 OS kernel: Goals + KPIs (deterministic evaluator), Tasks with
dependencies + append-only Executions, Agent/Skill/Tool/Model registries,
PermissionEngine (deny-by-default) + Approvals, MemoryPort, Runtime/Model/
Scheduler/Orchestrator ports with MockRuntime, usage tracking, and the
capstone kernel-flow integration test (Goal → Task → Dependency → Agent →
Execution → Events → KPI).

## Quickstart

Requirements: Python 3.12+ (Docker optional — only needed for PostgreSQL).

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Configure (SQLite works with zero setup; use Postgres via docker compose)
cp .env.example .env

# 3a. With Docker (PostgreSQL + API):
docker compose up          # API on http://localhost:8000, docs at /docs
# 3b. Without Docker (SQLite):
make dev                   # API on http://localhost:8000

# 4. Seed demo data (optional)
make seed
```

## Try the API

```bash
# Register + login
curl -s -X POST http://localhost:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"founder@example.com","password":"super-secret-1","display_name":"Founder"}'

TOKEN=<access_token from the response>

# Create an organization (you become its OWNER)
curl -s -X POST http://localhost:8000/organizations \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Acme Holdings","slug":"acme-holdings"}'

# Create a company instance inside it (business-agnostic:
# identity/mission/strategy are JSON payloads owned by the company)
curl -s -X POST http://localhost:8000/organizations/$ORG_ID/companies \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Acme SaaS","slug":"acme-saas","mission":"Ship the platform."}'

# Activate it (lifecycle state machine: draft → active ⇄ paused → archived)
curl -s -X POST http://localhost:8000/organizations/$ORG_ID/companies/$COMPANY_ID/status \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"status":"active"}'
```

Full endpoint reference: interactive at `/docs` (dev) — RFC 9457
problem-details errors everywhere.

## Development

```bash
make lint        # ruff check + format check
make typecheck   # mypy strict
make test        # pytest (SQLite in-memory; no Docker needed)
make layering    # enforce core-layer import rules
make migrate     # alembic upgrade head (needs Postgres URL)
```

CI runs lint, layer-boundary check, mypy strict, and the full test suite on
every push/PR.

## Repository layout

```
polsia/
├── core/        Domain entities, invariants, ports (interfaces) — zero I/O deps
├── adapters/    Port implementations: persistence, events, security, (runtime: Phase 3)
├── services/    Application use cases: authorization + event emission
├── api/         FastAPI routers, schemas, auth, tenant context
└── infra/       Wiring: settings, logging, db, seed
```

Layer rule: `core` imports nothing above it (CI-enforced). The future
business is a configuration/state instance — the core is business-agnostic.
