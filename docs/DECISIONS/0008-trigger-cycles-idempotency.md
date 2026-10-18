# ADR-0008 — Bound event-trigger cycles and suppress duplicate or recursive delivery

**Status:** Superseded by ADR-0011 for delivery recovery; the cycle-ID,
allowlisting, recursion-suppression, and untrusted-payload decisions remain in
force · **Date:** 2026-09-23

## Context

At-least-once event delivery can repeat notifications, and an action can emit an
event that would otherwise feed back into its own trigger. Trigger execution
must not depend on untrusted event payload data or introduce an externally
invocable replay path.

## Decision

- Trigger policies are internal, disabled by default, and match only an
  explicit event-type allowlist (`task.ready` and `task.failed`).
- A delivery claim is unique by `(policy_id, event_id)`. The first accepted
  delivery associates a stable trigger cycle ID with that pair; any existing
  row is terminal deduplication state, including `claimed` and `failed`. A
  duplicate delivery, including replay of the same stored event, is suppressed
  and does not start another cycle. There is no lease, automatic retry, or
  public/implicit force-replay operation.
- Trigger lineage is propagated with generated events. Events identified as
  recursive in the same trigger lineage are suppressed, with their causal
  event/cycle keys retained for audit.
- Event payloads are untrusted inputs. They cannot choose task IDs, permissions,
  tools, or runtime instructions for the autonomous loop.
- Multiple decisions belong to one cycle; `DecisionRecord.cycle_id` is not a
  uniqueness key. Cycle-level query summaries are derived from persisted
  DecisionRecords rather than a separate presumed cycle record.

## Consequences

- Delivery claiming must be atomic and durable to make the uniqueness rule
  effective under concurrent notifications.
- The dispatcher deliberately provides at-most-once attempts, not recoverable
  at-least-once execution. A process crash after claiming but before work, or
  during work, can leave a `claimed` row and lose or partially apply the cycle;
  a recorded `failed` row is also not retried. Catch-up excludes every event
  with a delivery row.
- Lease recovery is deferred until side effects can be fenced and made
  idempotent; safe redelivery is distinct from an operator-requested replay.
  No public replay or trigger mutation endpoint is part of the design.
- A missing delivery claim or unresolved tenant scope must fail closed rather
  than invoke autonomous execution.
