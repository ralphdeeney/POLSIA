# ADR-0012 — Bound observation task reads in the query and version the digest

**Status:** Accepted · **Date:** 2026-10-18

## Context

The observer's response lists were capped, but it loaded the complete
tenant-scoped task list and its digest hashed every task row. On a company with
a large task table this is an `O(tasks)` read plus an `O(tasks)` hash on every
observation — the dominant scaling risk in the OBSERVE pipeline. ADR-0010 and
ADR-0008 recorded this as deferred scalability work: bounded count/top-N queries
and a separate digest redesign.

## Decision

- `TaskRepository.list_for_company` accepts keyword-only `status` and `limit`.
  The SQLAlchemy adapter pushes both into the tenant-scoped query and preserves
  the deterministic `ORDER BY created_at, id`.
- `TaskRepository.count_for_company` returns the uncapped total with a
  tenant-scoped `COUNT(*)`, without materializing rows.
- The observer fetches ready/pending/running/failed slices with the configured
  limits, resolves blocked-task dependencies with point reads (`get`), and uses
  `count_for_company` for `tasks_total`.
- The observation digest is versioned to **v2**. It hashes the *bounded*
  observation content plus totals (`v`, `goals_total`, `kpis_total`,
  `tasks_total`) instead of every task row. Version 1 hashed all tasks, which
  forced the unbounded read. Recorded decision records remain valid historical
  v1 digests; the digest is an opaque correlation value, not a cross-version
  equality key.
- Goals, KPIs, agents, and approvals remain unbounded reads: they are small by
  design and their digest inputs were already bounded.

## Consequences

- Observation task reads are bounded by the configured limits plus one count
  query, independent of task-table size. Deterministic ordering is preserved.
- Blocked-task detection is derived from the bounded pending slice, so blocked
  tasks are a subset of the displayed pending tasks.
- Digests for the same state differ across digest versions; comparisons must be
  made within one version.
- This is a bounded-read guarantee with complexity evidence, not a measured
  benchmark.

## Verification

`tests/services/test_observation.py` adds a repository spy asserting every task
list query is status-filtered and bounded and that the total is a count query,
plus limit/boundary, tenant-isolation, and empty-dataset coverage.
