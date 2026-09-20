# ADR-0006 — Registry scoping: company-scoped domain, platform-global capabilities

**Status:** Accepted (approved decision C2) · **Date:** 2026-09-18

## Context
Phase 2 needs registries for Agents, Skills, Tools and Models. The spec
allows the scoping split to be decided by the implementation.

## Decision
- **Company-scoped** (tenant-isolated, uniqueness per company): Goals, KPIs,
  Tasks, Task executions, Agents, Permissions, Memory records, Approvals,
  Usage records.
- **Platform-global** (shared platform capabilities): Skills, Tools, Models —
  versioned and uniquely identifiable by `name@version`
  (e.g. `web-research@1.0.0`).

## Capability vs authorization principle
A globally registered Tool or Skill does **not** mean every company can use
it, and does **not** mean every agent can execute it. Registry availability
and authorization are separate concepts: the PermissionEngine
(allow/deny/approval_required, deny-by-default) gates agent → tool/action at
runtime. Registry interfaces are designed so future company-level
configuration, restrictions and overrides can be added without redesigning
the global registry.

## Consequences
- Executions record the exact skill version used (reproducibility).
- Skills/Tools/Models have no `company_id`; tenant context lives in the
  records that reference them.
- Registration is an authenticated platform operation (Phase 2 assumption:
  any authenticated user; operator administration is a later phase).
