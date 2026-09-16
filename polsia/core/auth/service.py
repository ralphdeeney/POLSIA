"""AuthService: registration and authentication use cases.

This module is pure core: it depends only on entities, the UserRepository
port (polsia.core.tenancy.ports) and the Protocols declared here. Concrete
PasswordHasher / TokenIssuer implementations live in polsia.adapters.security
and are injected, keeping core free of FastAPI/bcrypt/PyJWT imports
(enforced by scripts/check_layering.py).
"""

from __future__ import annotations

from typing import Protocol

from polsia.core.shared.errors import DuplicateEmail, InvalidCredentials
from polsia.core.shared.ids import UserId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import UserRepository

#: Error message used for every failed authentication so that unknown email,
#: wrong password and suspended accounts are indistinguishable from the
#: outside (no user enumeration).
_AUTH_FAILURE = "invalid email or password"


class PasswordHasher(Protocol):
    """Port for password hashing. Implemented by adapters (bcrypt)."""

    def hash(self, raw_password: str) -> str: ...

    def verify(self, raw_password: str, hashed_password: str) -> bool: ...


class TokenClaims(Protocol):
    """Structural view of verified token claims.

    Adapters return a concrete dataclass (polsia.adapters.security.tokens.Claims)
    with these attributes; core only needs the shape.
    """

    @property
    def sub(self) -> UserId: ...

    @property
    def exp(self) -> int: ...

    @property
    def iat(self) -> int: ...

    @property
    def typ(self) -> str: ...


class TokenIssuer(Protocol):
    """Port for JWT issuing/verification. Implemented by adapters (PyJWT)."""

    def issue_access_token(self, user_id: UserId) -> str: ...

    def issue_refresh_token(self, user_id: UserId) -> str: ...

    def verify_token(self, token: str) -> TokenClaims:
        """Verify signature/expiry and raise InvalidCredentials on any failure."""
        ...


class AuthService:
    """Domain-facing auth use cases. Raises domain errors only."""

    def __init__(
        self,
        users: UserRepository,
        hasher: PasswordHasher,
        tokens: TokenIssuer,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._tokens = tokens

    async def register(self, email: str, password: str, display_name: str) -> User:
        """Create a new user with a hashed password.

        Raises DuplicateEmail when the email is already taken.
        """
        existing = await self._users.get_by_email(email)
        if existing is not None:
            raise DuplicateEmail(f"email {email!r} is already registered")
        hashed = self._hasher.hash(password)
        user = User.register(email=email, hashed_password=hashed, display_name=display_name)
        await self._users.add(user)
        return user

    async def authenticate(self, email: str, password: str) -> User:
        """Return the user on success; InvalidCredentials on any failure.

        Unknown emails still run a hash verification so response timing does
        not reveal whether the account exists.
        """
        user = await self._users.get_by_email(email)
        if user is None:
            # Burn comparable time to a real bcrypt verify to equalize timing.
            self._hasher.verify(password, _DUMMY_HASH)
            raise InvalidCredentials(_AUTH_FAILURE)
        if not self._hasher.verify(password, user.hashed_password):
            raise InvalidCredentials(_AUTH_FAILURE)
        if not user.is_active():
            raise InvalidCredentials(_AUTH_FAILURE)
        return user

    def issue_token_pair(self, user: User) -> tuple[str, str]:
        """Convenience: (access_token, refresh_token) for a authenticated user."""
        return (
            self._tokens.issue_access_token(user.id),
            self._tokens.issue_refresh_token(user.id),
        )


#: A real bcrypt hash of random data, used only to equalize verify timing for
#: unknown emails. Never matches a user-supplied password in practice.
_DUMMY_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEe.6uZf5oqL0Kx0zA7X0gQm9lR2eB6Uu1q"
