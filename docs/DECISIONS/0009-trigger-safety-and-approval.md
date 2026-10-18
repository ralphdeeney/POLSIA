# ADR-0009 — Apply bounded SafetyGuard checks and preserve explicit approval

**Status:** Accepted · **Date:** 2026-09-23

## Context

An event-triggered decision can lead to an execution attempt. Before dispatch,
the system must revalidate scope and current state, bound the work it can
cause, and preserve the existing permission and human-approval boundary.

## Decision

- Run `SafetyGuard` immediately before dispatch/execution creation. Reject
  disabled policies, non-allowlisted or stale/future-dated events,
  organization/company mismatches, inactive companies, mismatched decisions,
  and tasks that are not ready or retryable.
- Bound actions, tasks, executions, and retries per task. Defaults are
  conservative (3 actions, 3 tasks, 3 executions, 2 retries per task), and
  configured values cannot exceed hard ceilings (100 actions, 100 tasks, 100
  executions, 10 retries per task). The configured retry limit is the enforced
  limit; there is no separate hidden two-retry cap. Task `max_attempts` remains
  an independent upper bound. Event age and future clock skew are also bounded
  (defaults: 300 seconds and 30 seconds respectively).
- Require a non-empty observation digest and a trigger cycle key for dispatch.
  Only supported dispatch decisions (prioritize or retry) may pass the guard.
- Keep the shared execution authorization / PermissionEngine authoritative.
  **Only an explicit `ALLOW` permits execution.** `ExecutionService.start_execution`
  requires that boundary as a construction dependency and invokes it before any
  task transition or attempt write, so every caller gets identical deny and
  approval-required semantics. Approval-required is never automatically approved.
  Guard rejection and authorization both happen before runtime dispatch.
- The public task-start API calls that service-layer boundary and records only an
  authorized attempt; it does not invoke a runtime. Orchestration uses the same
  attempt service before runtime dispatch, not an independent authorization path.

## Consequences

- Policies cannot disable the explicit-ALLOW requirement.
- Safety checks complement rather than replace tenant authorization and
  task-attempt/retry limits already enforced by orchestration and task state.
- A limit or permission failure stops the trigger cycle safely; it does not
  imply an execution succeeded.
