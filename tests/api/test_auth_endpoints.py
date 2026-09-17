"""Auth endpoint tests: register -> login -> /auth/me over the full stack."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from polsia.api.main import create_app
from polsia.infra.db import create_all_metadata
from polsia.infra.settings import Settings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

PASSWORD = "super-secret-1"


def make_settings() -> Settings:
    return Settings(env="test", jwt_secret="test-secret")


@pytest.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        app = create_app(make_settings(), session_factory=factory)
        transport = ASGITransport(app=app)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        await engine.dispose()


async def register(
    client: AsyncClient,
    email: str,
    password: str = PASSWORD,
    display_name: str = "Test User",
) -> Any:
    return await client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
    )


def bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


class TestRegister:
    async def test_register_returns_user_and_tokens(self, client: AsyncClient) -> None:
        response = await register(client, "alice@example.com")

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["user"]["email"] == "alice@example.com"
        assert body["user"]["display_name"] == "Test User"
        assert body["user"]["id"].startswith("usr_")
        tokens = body["tokens"]
        assert tokens["token_type"] == "bearer"
        assert tokens["access_token"] and tokens["refresh_token"]
        assert tokens["expires_in"] == 30 * 60

    async def test_duplicate_email_conflicts(self, client: AsyncClient) -> None:
        await register(client, "bob@example.com")
        response = await register(client, "bob@example.com", "another-pass-1")

        assert response.status_code == 409
        assert response.json()["code"] == "duplicate_email"

    async def test_short_password_is_rejected(self, client: AsyncClient) -> None:
        response = await register(client, "carol@example.com", "short")

        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"


class TestLogin:
    async def test_login_returns_tokens(self, client: AsyncClient) -> None:
        await register(client, "dave@example.com")
        response = await client.post(
            "/auth/login", json={"email": "dave@example.com", "password": PASSWORD}
        )

        assert response.status_code == 200
        tokens = response.json()
        assert tokens["token_type"] == "bearer"
        assert tokens["access_token"] and tokens["refresh_token"]

    async def test_wrong_password_is_unauthorized(self, client: AsyncClient) -> None:
        await register(client, "erin@example.com")
        response = await client.post(
            "/auth/login", json={"email": "erin@example.com", "password": "wrong-pass-1"}
        )

        assert response.status_code == 401
        assert response.json()["code"] == "invalid_credentials"

    async def test_unknown_email_is_unauthorized(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/login", json={"email": "ghost@example.com", "password": PASSWORD}
        )
        assert response.status_code == 401


class TestMe:
    async def test_me_with_valid_token(self, client: AsyncClient) -> None:
        registered = (await register(client, "frank@example.com")).json()
        response = await client.get("/auth/me", headers=bearer(registered["tokens"]))

        assert response.status_code == 200
        assert response.json()["email"] == "frank@example.com"
        assert response.json()["id"] == registered["user"]["id"]

    async def test_me_without_token_is_unauthorized(self, client: AsyncClient) -> None:
        response = await client.get("/auth/me")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_me_with_garbage_token_is_unauthorized(self, client: AsyncClient) -> None:
        response = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_refresh_token_is_not_accepted_as_access(self, client: AsyncClient) -> None:
        registered = (await register(client, "gina@example.com")).json()
        response = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {registered['tokens']['refresh_token']}"},
        )
        assert response.status_code == 401
