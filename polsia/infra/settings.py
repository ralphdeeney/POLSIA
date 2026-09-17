"""Application settings (12-factor, pydantic-settings).

Every environment-specific value comes from `POLSIA_*` environment variables.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: HS256 keys must be >= 32 bytes (RFC 7518 §3.2); the default dev secret is
#: rejected outright outside development.
MIN_JWT_SECRET_BYTES = 32
_DEFAULT_JWT_SECRET = "dev-only-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLSIA_", env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"

    # SQLite (memory) by default so the OS runs/tests without Docker or
    # Postgres; production/docker-compose sets a postgres+asyncpg URL.
    database_url: str = "sqlite+aiosqlite://"

    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14

    @field_validator("jwt_secret")
    @classmethod
    def _validate_jwt_secret(cls, value: str, info: ValidationInfo) -> str:
        # Enforced only in production; dev/test keep the default convenience.
        if info.data.get("env") == "production":
            if value == _DEFAULT_JWT_SECRET:
                raise ValueError("POLSIA_JWT_SECRET must be changed from the default in production")
            if len(value.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
                raise ValueError(
                    f"POLSIA_JWT_SECRET must be at least {MIN_JWT_SECRET_BYTES} bytes "
                    "for HS256 (RFC 7518 §3.2)"
                )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
