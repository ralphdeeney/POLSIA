# CONTRIBUTING.md — Subagent Orchestration Policy

This repository is built by a Lead Architect (orchestrating agent) with
scoped subagents. The rules below are binding.

## Lead Architect responsibilities
1. Own all architecture decisions (`docs/ARCHITECTURE.md` is the source of truth).
2. Decompose work into independent tasks with explicit scope.
3. Dispatch subagents (max **5 concurrent**), never for trivial work.
4. Review every subagent result before integration. Unreviewed code is not merged.
5. Own integration, test runs, and fixes.

## Subagent brief — required fields
Every subagent receives **all** of:
- **Objective** — what to build, in one paragraph.
- **Scope** — exact files/directories it may create or modify.
- **Relevant context** — architecture rules, related files, ADRs.
- **Constraints** — layer boundaries, business-agnosticism, style, no unrelated changes.
- **Expected output** — deliverable file list + summary of decisions made.
- **Validation** — the commands/tests that must pass before handoff.

## Subagent constraints
- No file outside declared scope; no refactors beyond the task.
- `polsia/core` must not import `api`/`adapters`/`services`/`infra`
  (CI-enforced via `scripts/check_layering.py`).
- No business/industry-specific logic in core.
- Do not duplicate another active subagent's task; check the task board first.
- Report failures honestly; do not weaken tests to pass.

## Current subagent role roster
System Architect · Backend Engineer · Database Engineer · Agent System
Engineer · Orchestration Engineer · Memory Engineer · Skills Engineer ·
Tool/MCP Engineer · Frontend Engineer · Security Engineer · QA Engineer ·
DevOps Engineer · Code Reviewer.

Not all run simultaneously; the Lead selects per task.
