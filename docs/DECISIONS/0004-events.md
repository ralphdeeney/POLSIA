# ADR-0004 — Events: stable envelope, in-process bus now, durable bus later

**Status:** Accepted · **Date:** 2026-09-16

## Decision
All domain events share one envelope:
`{id, type, occurred_at, organization_id, company_id, payload}`.
Phase 1 ships an in-process pub/sub `EventBus` adapter; Phase 2 may replace
it with a durable bus **without changing services or the envelope contract**.

## Consequences
- Consumers (logging now; registries, orchestrator, CEO later) subscribe to
  the port, never to an implementation.
- At-least-once delivery semantics must be assumed by consumers from Phase 2
  onward; handlers must be idempotent.
