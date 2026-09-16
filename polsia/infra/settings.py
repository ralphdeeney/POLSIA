"""Application settings (12-factor, pydantic-settings).

Every environment-specific value comes from `POLSIA_*` environment variables.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLSIA_", env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"

    # SQLite (memory) by default so the OS runs/tests without Docker or
    # Postgres; production/docker-compose sets a postgres+asyncpg URL.
    database_url: str = "sqlite+aiosqlite://"

    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14


@lru_cache
def get_settings() -> Settings:
    return Settings()
