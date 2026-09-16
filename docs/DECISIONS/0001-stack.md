# ADR-0001 — Technology stack: Python + FastAPI

**Status:** Accepted · **Date:** 2026-09-16

## Context
The OS core must support agent runtimes, LLM-driven components, registries,
and typed multi-tenant APIs. One language across core and future dashboard
was an alternative (TypeScript/NestJS).

## Decision
Python 3.12+ with FastAPI, SQLAlchemy 2 (async) + Alembic, Pydantic v2,
pytest, ruff + mypy strict. The future Phase 6 dashboard is a separate
frontend and does not constrain this choice.

## Consequences
- Strongest agent/AI ecosystem for runtime adapters and the CEO planner.
- Async SQLAlchemy gives asyncpg performance for Postgres; aiosqlite keeps
  local dev/test runnable without Docker (dev machines without Docker).
- mypy strict + Pydantic v2 give the type safety a TS stack would provide.
