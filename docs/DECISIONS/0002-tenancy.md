# ADR-0002 — Tenancy model: Organization-scoped, explicit tenant parameter

**Status:** Accepted · **Date:** 2026-09-16

## Context
Multiple independent organizations and companies must coexist; cross-tenant
leakage is the primary security risk.

## Decision
Hierarchy User —(membership+role)— Organization —1:N— Company.
Every company-scoped repository/service call takes the organization scope as
an **explicit mandatory parameter**; there is no ambient tenant. API requests
authorize the (user, org) pair via OrgMembership before reaching services.

## Consequences
- No hidden global tenant state; repositories are trivially testable.
- Isolation is enforced in one layer (service authz) and proven by a
  dedicated test suite; future company-level tenancy builds on the same rule.
