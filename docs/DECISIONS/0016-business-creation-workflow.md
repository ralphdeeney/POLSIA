# ADR-0016: Business idea intake, validated blueprints and company bootstrap

Status: accepted (Phase 4, wave 3)

## Context

Phase 4 needed the missing front of the product: turning an owner's business
idea into a real, operating company inside the existing control plane. The
risks were (a) inventing a second business/tenant state system, (b) letting
model output become authoritative, (c) fabricating market evidence, and (d)
bypassing the Phase 2/3 authorization and execution boundaries.

## Decision

1. **A pure domain layer** (`polsia/core/business/`) defines `BusinessIdea`
   (raw text preserved verbatim, inferred requirements kept separate, explicit
   intake/blueprint lifecycle) and a typed `CompanyBlueprint` with structural,
   internal-consistency and evidence-honesty validation. Supported business
   model families cover SaaS, e-commerce, agency, AI automation, local
   services, digital education, marketplace, subscription, physical product and
   `other`; nothing assumes SaaS.
2. **One business state system**: the blueprint payload/version lives on the
   `BusinessIdea` row (`alembic/versions/0012_business_ideas.py`). There is no
   second company or strategy store; bootstrap writes through the existing
   `CompanyService`/`GoalService`/`KpiService`/`AgentService`/`MemoryService`.
3. **Blueprint generation is a port** (`BlueprintPort`) with two adapters:
   - `DeterministicBlueprintGenerator` — offline, evidence-honest, the default
     for CI, offline E2E and fallback;
   - `ModelBackedBlueprintGenerator` — reuses the existing `ModelPort`, runs a
     fixed versioned prompt plan with per-call/total token, time and call
     budgets, bounded schema-repair retries, and produces a *proposal* only.
     Intermediate outputs (business family, problem framing, missing details)
     are carried into later prompts, and the composing prompt repeats budget,
     conflicting-constraint and geographic constraints so they cannot be
     silently dropped. Schema-repair feedback includes the concrete parse
     error. No new LLM client exists.
   A proposal becomes usable only after `CompanyBlueprint.validate()` and the
   artifact verifier pass and the payload is durably persisted.
4. **Evidence honesty is enforceable**: when `research_used` is false, any
   `verified_fact`/`sourced_observation` claim in market, pricing, competitive
   or financial sections is a validation error. The deterministic path labels
   these claims hypothesis/unknown.
5. **Bootstrap is atomic-or-resumable and idempotent** by
   `bootstrap:{organization_id}:{idea_id}`: the company strategy carries
   `bootstrap_idea_id`, and a retry re-uses the existing company instead of
   duplicating companies, goals, KPIs, agents or memory. The service never
   registers a legal entity, buys domains, spends money or activates paid
   accounts.
6. **CEO initialization** configures the bootstrap-created CEO agent from
   trusted company state (identity, blueprint, goals, KPIs, constraints,
   roster, budgets/approval policy) and produces the initial plan through the
   existing deterministic `PlanningEngine`; tasks reference real goals and are
   materialized only after `Plan.validate()`.
7. **Verification stays task-type aware** (`ArtifactVerifier`,
   `ResearchVerifier`): artifact tasks complete on schema/section/consistency/
   ownership/persistence/provenance checks; external actions still require
   observable evidence from trusted tools. Blueprint generation never has to
   invoke an external tool to succeed.
8. **Authorization is unchanged**: the HTTP routers are thin; services enforce
   membership/tenant scope; the first execution of bootstrap work goes through
   `OrchestrationService.execute_task` and its permission-engine gate.

## Alternatives rejected

- A separate `blueprints` table or a second company-state store: duplicates the
  idea lifecycle and invites drift.
- Trusting model output as the artifact: rejected; validation + verification +
  persistence gate every proposal.
- Requiring external research/tools for blueprint completion: would make
  artifact tasks impossible offline and incentivise fabricated "evidence".
- Copying a template company (members/credentials/memory) into the new one:
  rejected on tenant-isolation grounds; only blueprint-derived data is used.

## Limitations

- Model-backed blueprint generation and CEO planning are optional. The
  production adapter was exercised live through an OpenAI-compatible 9router
  endpoint (`cx/gpt-5.6-sol`): 14/15 in the first continuous pass, then 15/15
  merged after the sole malformed-JSON failure passed on a targeted retry
  following the schema-repair improvement. Gemini 3.8 Flash passed the bounded
  smoke test; the full Gemini suite was quota-blocked by the free tier (20
  requests/day). An OpenCode/DeepSeek bridge was used for early prompt testing;
  it is test-only and does not exercise the production transport. Live
  model-backed CEO planning was **NOT RUN**.
- PostgreSQL transaction/concurrency verification was **NOT RUN** in this
  environment; SQLite covers functional and idempotency behaviour.
- Blueprint quality is validated structurally and against deterministic
  scenarios; real-world demand and pricing remain unvalidated hypotheses. No
  live run registered a legal entity, bought a domain, spent money or activated
  a paid account.

## Verification

- Domain/service tests (`tests/services/test_business_workflow.py`), API tests
  (`tests/api/test_business_api.py`), model-adapter tests
  (`tests/adapters/test_model_backed_blueprint.py`).
- Deterministic 15-scenario blueprint evaluation
  (`polsia/eval/business.py`, `scripts/run_business_evaluations.py`,
  `tests/eval/test_blueprint_evaluation.py`), reproducible report digest.
- Live 15-scenario evaluation through the production
  `OpenAICompatibleModelProvider` against a 9router endpoint
  (`cx/gpt-5.6-sol`): three contiguous chunks plus one targeted retry, merged
  15/15, report digest `a95c47a9e61110ed`. The first continuous pass was
  14/15; the malformed-JSON budget scenario is preserved as a retry, not
  rewritten. Prompt version, provider model and redacted key metadata are
  recorded in the report.
- 16-step end-to-end flow through real persistence, one bounded
  `NativeAgentRuntime` execution, observation, tenant-isolation matrix and
  negative paths (`tests/integration/test_business_creation_e2e.py`).
- Performance/cost measurements (`tests/integration/test_business_performance.py`).
- Migration round-trip: `alembic upgrade head` then `downgrade base` on SQLite.
