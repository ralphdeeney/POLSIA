# ADR-0007 — Persist validated CEO decisions as append-only records

**Status:** Accepted · **Date:** 2026-09-23

## Context

The bounded CEO loop needs an auditable history of what it decided and the
outcome observed for each decision. Phase 3 Slice 6 implements the
`DecisionRecord` domain type, tenant-scoped repository, and SQLAlchemy
`decision_records` table.

## Decision

- Persist each validated typed decision as an append-only `DecisionRecord`.
  A record is scoped by both `organization_id` and `company_id` and contains
  the decision type/payload, cycle ID, observation digest, outcome context,
  and decision/record timestamps; rationale, confidence, and model reference
  are retained when present.
- The repository supports append and company reads scoped by both organization
  and company. Append validates that the referenced company exists and belongs
  to the record organization before writing. Writes flush in the caller's
  transaction; the caller owns the transaction boundary.
- Persist outcomes alongside the decision record rather than treating an
  in-memory loop result as the audit source. A cycle can contain multiple
  records, so `cycle_id` is not unique.
- The combined `payload` and `outcome_context` must serialize as compact,
  sorted UTF-8 JSON no larger than 8 KiB. `outcome_context` must be a JSON
  object, not an array or scalar. Domain construction and response DTO
  validation enforce the same ceiling; non-finite and non-JSON values are
  rejected.
- These records are not a general event log and do not by themselves assert a
  separately persisted cycle summary, stop reason, or action count.

## Consequences

- Decision history can be read without reconstructing decisions from mutable
  task state, and tenant scope is explicit in persistence queries.
- Payload and outcome context must remain JSON-serializable; decision
  validation is required before persistence.
- Phase 3 Slice 6 provides this persistence. API exposure is separately
  bounded by ADR-0010.
