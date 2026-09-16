# ADR-0003 — Identity: prefixed ULIDs, typed at the domain layer

**Status:** Accepted · **Date:** 2026-09-16

## Decision
Entity IDs are ULIDs with semantic prefixes (`usr_…`, `org_…`, `cmp_…`),
wrapped in distinct domain types (`UserId`, `OrganizationId`, `CompanyId`)
so they cannot be mixed accidentally. Stored as TEXT.

## Consequences
- Sortable by creation time without a join; safe to embed in logs/URLs.
- Wrong-ID-type bugs become type errors under mypy strict.
- ULIDs are index-friendly compared to random UUIDv4s.
