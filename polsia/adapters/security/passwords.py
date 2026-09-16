"""bcrypt password hashing adapter (implements core PasswordHasher port)."""

from __future__ import annotations

import bcrypt

#: bcrypt's default cost factor is 10; we use 12 for extra margin. This is a
#: deliberate constant — do not lower it without a security review.
_DEFAULT_ROUNDS = 12


class BcryptPasswordHasher:
    """Hash/verify passwords with bcrypt.

    Verification goes through ``bcrypt.checkpw``, which reads the salt and
    cost from the stored hash and compares in constant time with respect to
    the secret bytes.
    """

    def __init__(self, rounds: int = _DEFAULT_ROUNDS) -> None:
        self._rounds = rounds

    def hash(self, raw_password: str) -> str:
        salt = bcrypt.gensalt(rounds=self._rounds)
        hashed = bcrypt.hashpw(raw_password.encode("utf-8"), salt)
        return hashed.decode("ascii")

    def verify(self, raw_password: str, hashed_password: str) -> bool:
        try:
            return bcrypt.checkpw(raw_password.encode("utf-8"), hashed_password.encode("ascii"))
        except ValueError:
            # Malformed/invalid stored hash — treat as verification failure
            # rather than leaking an infrastructure error.
            return False
