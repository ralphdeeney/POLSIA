"""AuthService tests with in-memory fakes (no adapters, no I/O)."""

from __future__ import annotations

import dataclasses

import pytest
from polsia.core.auth.service import AuthService
from polsia.core.shared.errors import DuplicateEmail, InvalidCredentials
from polsia.core.shared.ids import UserId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.values import UserStatus


class FakeUserRepository:
    """In-memory UserRepository implementing the tenancy port."""

    def __init__(self) -> None:
        self._by_id: dict[UserId, User] = {}
        self._by_email: dict[str, User] = {}

    async def add(self, user: User) -> None:
        self._by_id[user.id] = user
        self._by_email[user.email] = user

    async def get(self, user_id: UserId) -> User | None:
        return self._by_id.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return self._by_email.get(email.strip().lower())

    async def update(self, user: User) -> None:
        self._by_id[user.id] = user
        self._by_email[user.email] = user


class FakeHasher:
    """Deterministic PasswordHasher fake (no real crypto)."""

    def hash(self, raw_password: str) -> str:
        return f"hashed:${len(raw_password)}$:{raw_password[::-1]}"

    def verify(self, raw_password: str, hashed_password: str) -> bool:
        return hashed_password == self.hash(raw_password)


class FakeTokenIssuer:
    """TokenIssuer fake recording issued ids."""

    def __init__(self) -> None:
        self.issued: list[UserId] = []

    def issue_access_token(self, user_id: UserId) -> str:
        self.issued.append(user_id)
        return f"access-token-for-{user_id}"

    def issue_refresh_token(self, user_id: UserId) -> str:
        self.issued.append(user_id)
        return f"refresh-token-for-{user_id}"

    def verify_token(self, token: str) -> UserId:  # pragma: no cover - unused here
        raise NotImplementedError


@pytest.fixture
def repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def service(repo: FakeUserRepository) -> AuthService:
    return AuthService(repo, FakeHasher(), FakeTokenIssuer())


async def test_register_hashes_password_and_persists(
    repo: FakeUserRepository, service: AuthService
) -> None:
    user = await service.register("Alice@Example.com", "super-secret-1", "Alice")

    assert user.id.startswith("usr_")
    assert user.email == "alice@example.com"  # normalized
    assert user.hashed_password.startswith("hashed:")
    assert "super-secret-1" not in user.hashed_password
    # Persisted and retrievable by email.
    stored = await repo.get_by_email("alice@example.com")
    assert stored is not None and stored.id == user.id


async def test_register_duplicate_email_raises_duplicate_email(
    service: AuthService,
) -> None:
    await service.register("bob@example.com", "super-secret-1", "Bob")
    with pytest.raises(DuplicateEmail):
        await service.register("bob@example.com", "another-password", "Bob 2")


async def test_authenticate_ok(service: AuthService) -> None:
    created = await service.register("carol@example.com", "super-secret-1", "Carol")
    user = await service.authenticate("carol@example.com", "super-secret-1")
    assert user.id == created.id


async def test_wrong_password_raises_invalid_credentials(service: AuthService) -> None:
    await service.register("dave@example.com", "super-secret-1", "Dave")
    with pytest.raises(InvalidCredentials):
        await service.authenticate("dave@example.com", "wrong-password")


async def test_suspended_user_raises_invalid_credentials(
    repo: FakeUserRepository, service: AuthService
) -> None:
    created = await service.register("erin@example.com", "super-secret-1", "Erin")
    suspended = dataclasses.replace(created, status=UserStatus.SUSPENDED)
    await repo.update(suspended)

    with pytest.raises(InvalidCredentials):
        await service.authenticate("erin@example.com", "super-secret-1")


async def test_unknown_email_raises_invalid_credentials_no_enumeration(
    service: AuthService,
) -> None:
    with pytest.raises(InvalidCredentials):
        await service.authenticate("nobody@example.com", "whatever-password")


async def test_issue_token_pair_uses_token_issuer(service: AuthService) -> None:
    user = await service.register("frank@example.com", "super-secret-1", "Frank")
    access, refresh = service.issue_token_pair(user)
    assert access == f"access-token-for-{user.id}"
    assert refresh == f"refresh-token-for-{user.id}"
