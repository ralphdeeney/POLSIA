"""Async database engine, session factory and session provider.

This module is DI-agnostic: `get_session` is a plain async-generator provider
(FastAPI dependency wiring happens in the API layer, which may import infra).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from polsia.infra.settings import Settings


def make_engine(
    database_url: str | None = None, *, settings: Settings | None = None
) -> AsyncEngine:
    """Create the async engine.

    `database_url` overrides `Settings.database_url` (which itself reads the
    `POLSIA_DATABASE_URL` environment variable).
    """
    url = database_url or (settings or Settings()).database_url
    kwargs: dict[str, object] = {}
    if not url.startswith("sqlite"):
        # Production (PostgreSQL): drop dead connections eagerly.
        kwargs["pool_pre_ping"] = True
    return create_async_engine(url, **kwargs)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory. `expire_on_commit=False` so entities remain usable
    after commit without lazy reloads (attributes are read eagerly)."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session(
    engine_or_factory: AsyncEngine | async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Plain provider yielding a transactional session.

    Commits on clean exit, rolls back on error, always closes. Repositories
    only flush; this provider owns the transaction boundary.
    """
    factory = (
        engine_or_factory
        if isinstance(engine_or_factory, async_sessionmaker)
        else make_session_factory(engine_or_factory)
    )
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all_metadata(engine: AsyncEngine, *, metadata: object | None = None) -> None:
    """Create all tables from the ORM metadata (no Alembic).

    Used by tests and local dev on SQLite. By default this resolves the ORM
    metadata from `polsia.adapters.persistence.models` via a *lazy, function-
    local* import — the only deliberate upward lookup in `polsia.infra`, kept
    here so the layering checker's core-purity guarantee stays intact and
    production code paths never depend on adapters. Pass `metadata=` explicitly
    to keep this module fully decoupled.
    """
    if metadata is None:
        from polsia.adapters.persistence import (
            decision_models as _decision_models,
        )
        from polsia.adapters.persistence import (
            trigger_models as _trigger_models,
        )
        from polsia.adapters.persistence.models import Base

        _ = (_decision_models, _trigger_models)
        metadata = Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn))  # type: ignore[attr-defined]
