# ADR-0011 — Leased, fenced, bounded at-least-once trigger deliveries

**Status:** Accepted · **Date:** 2026-10-18
**Supersedes:** the terminal at-most-once delivery decision in ADR-0008
(trigger cycle IDs, allowlisting, recursion suppression, and untrusted-payload
rules from ADR-0008 remain in force).

## Context

ADR-0008 chose terminal at-most-once claims: any existing `(policy_id, event_id)`
row — including a `claimed` row stranded by a crashed worker — permanently
suppressed redelivery. That is safe against duplicate side effects but loses
work after a crash, which the Phase 3 audit flagged as the remaining reliability
gap. Recovery must not weaken authorization, tenancy, or the bounded-loop
invariants.

## Decision

- Delivery rows gain `attempts`, `lease_owner`, `lease_expires_at`, and a
  monotonic `fencing_token` (migration `0011`), with an index on
  `(status, lease_expires_at)`.
- `claim` grants a lease. The first claim inserts the unique
  `(policy_id, event_id)` row (`attempts=1`, `fencing_token=1`). A later claim
  may take over **only** a retryable row (`interrupted`) or a `claimed` row whose
  lease has expired, and **only** while `attempts < max_attempts`; every takeover
  increments `attempts` and `fencing_token`.
- `finish` is fenced on `fencing_token`. A stale worker that lost its lease
  updates zero rows and cannot overwrite the newer owner's recorded outcome.
- Retries are bounded. A transient worker fault marks the row `interrupted`
  (reclaimable) while attempts remain; the attempt that exhausts the budget
  records `failed` (terminal). Deterministic rejections — permission,
  validation, tenant, event-type, and recursion — record `rejected` /
  `recursion_suppressed` and are never retried.
- Terminal statuses (`completed`, `failed`, `rejected`,
  `recursion_suppressed`) are never reclaimed. Catch-up excludes live leases and
  terminal rows, so they never consume the bounded catch-up budget.
- The stable trigger cycle ID remains the idempotency key for business effects.
- Authorization, tenancy, allowlist, and safety checks are unchanged and still
  run before any cycle work.

## Consequences

- The target is **at-least-once** processing with idempotent business effects,
  **not** exactly-once. If a lease expires while a long cycle is still running,
  another worker may start the same cycle concurrently. The fencing token stops
  the stale worker from *recording* its result, but it does not fence an
  already-performed effect. The task state machine limits the primary effect (a
  task already running or completed cannot start again), and effects must remain
  idempotent keyed by the cycle ID.
- `lease_seconds` and `max_delivery_attempts` are dispatcher configuration and
  are clamped (`1..3600` seconds, `1..10` attempts).
- Concurrency safety relies on the unique `(policy_id, event_id)` constraint plus
  a row lock (`SELECT ... FOR UPDATE`) on takeover for PostgreSQL. Behavior is
  validated on SQLite here; PostgreSQL row-lock execution is not run in CI.
- The dispatcher remains an explicitly injected internal dependency. No public
  replay or trigger-mutation route is added.

## Verification

`tests/services/test_event_triggers.py::TestTriggerLeaseRecovery` covers
expired-lease takeover with a higher token, stale-worker finish rejection,
crash-before-completion recovery via catch-up, bounded retry then terminal,
cross-company dispatch isolation, and concurrent claims granting exactly one
lease. `tests/services/test_event_triggers.py` also retains the replay/recursion
suppression coverage.
