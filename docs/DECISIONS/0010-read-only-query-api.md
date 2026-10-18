# ADR-0010 — Expose Phase 3 history and observation through authenticated GET only

**Status:** Accepted · **Date:** 2026-09-23

## Context

Phase 3 needs a safe way to inspect decisions, cycles, and current company
state without making query access an execution or trigger-control surface.
Decision history and observations are tenant-sensitive.

## Decision

- Any Phase 3 query endpoints are authenticated and nested under the
  organization/company resource path. They use GET only and bounded request
  limits / response DTOs.
- Provide read-only decision history, cycle summaries, and an observation
  summary. Derive cycle summaries from persisted DecisionRecords grouped by
  cycle ID; do not report a persisted stop reason or action count unless those
  fields are actually stored.
- Observation DTO lists have response caps. Count fields report uncapped totals;
  in particular, `kpis_total` remains the actual KPI count when the returned KPI
  list is truncated. The current observer still first loads full tenant-scoped
  goal, KPI, task, agent, and approval lists, and the digest processes every
  task. These bounds do not bound repository reads or digest work. Defer bounded
  count/top-N query APIs and a separate digest redesign so this slice does not
  change observation semantics.
- The query service verifies organization membership and company ownership
  before it calls the observer or reads decision records. Tenant-scoped
  repository filters remain required as defense in depth.
- Do not expose POST/PUT/PATCH/DELETE operations to start, replay, or otherwise
  trigger execution; do not expose trigger-policy mutation. Do not add
  business-specific public routes.

## Consequences

- Query access reveals bounded DTOs, not domain entities or unbounded database
  data.
- Authentication alone is insufficient: membership and organization/company
  ownership checks must precede data access to prevent cross-tenant leakage.
- This ADR defines the approved Slice 8 boundary; it does not claim the query
  service or routes are implemented until code provides them.
