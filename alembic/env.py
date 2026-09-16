"""Alembic environment (async engine template).

URL resolution order:
1. `alembic -x db_url=<url> upgrade head`   (explicit override)
2. `sqlalchemy.url` in alembic.ini, if not the placeholder
3. `Settings.database_url` (reads POLSIA_DATABASE_URL env var / .env)
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from polsia.adapters.persistence.models import Base
from polsia.infra.settings import Settings
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Alembic Config object; provides access to values within alembic.ini.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model metadata for autogenerate support.
target_metadata = Base.metadata

PLACEHOLDER = "__POLSIA_DATABASE_URL__"


def _resolve_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    url = x_args.get("db_url")
    if url:
        return url
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url and ini_url != PLACEHOLDER:
        return ini_url
    return Settings().database_url


config.set_main_option("sqlalchemy.url", _resolve_url())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: emit SQL to stdout without a DB connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode through an async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
