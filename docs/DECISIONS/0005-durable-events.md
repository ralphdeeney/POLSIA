# ADR-0005 — Durable events: synchronous EventBus preserved, EventStore port added

**Status:** Accepted · **Date:** 2026-09-18

## Context
Phase 2 requires durable event persistence (approved decision C1a) without
breaking the Phase 0–1 event contract. The existing `EventBus.publish` is
synchronous and fire-and-forget; a durable bus must persist each envelope —
an async operation.

## Decision
1. **The synchronous `EventBus` contract is intentionally preserved.** The
   port is not migrated to async in Phase 2; all existing consumers and the
   in-process adapter are untouched.
2. A separate **`EventStore` port** (`append`, `list_for_organization`,
   `list_for_company`) is added, implemented by the persistence adapter
   (`SqlAlchemyEventStore` over the `events` table).
3. The shared publish path becomes:
   `mutation → create EventEnvelope → persist (await store.append) → dispatch
   (bus.publish)`. Phase 2 services implement this via the `emit()` helper in
   `polsia.core.shared.events`; Phase 0–1 services (tenancy) keep their
   original synchronous publish (untouched).
4. `EventEnvelope` is extended **additively** with optional
   `aggregate_type`, `aggregate_id`, `actor`, `metadata` — existing fields,
   consumers and tests remain compatible.

## Consequences
- **Transactional consistency:** `append` is flush-only inside the caller's
  transaction — a rolled-back mutation leaves no event behind (the events
  table is trustworthy). Persistence failure aborts the mutation rather than
  silently dropping the event.
- **At-least-once semantics** apply to in-process subscribers from Phase 3
  onward; handlers must remain idempotent (already the convention).
- A fully durable bus (outbox dispatcher, retries) can later replace the
  in-process adapter behind the same port without touching services.
- Known tradeoff: Phase 0–1 (tenancy) events are dispatched but not durably
  persisted until those services adopt `emit` (documented technical debt).
