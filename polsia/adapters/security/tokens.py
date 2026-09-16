"""PyJWT token issuing/verification adapter (implements core TokenIssuer port).

Configuration (secret, algorithm, TTLs) comes from
polsia.infra.settings.Settings. All failures — expired, bad signature,
malformed, wrong type — raise the domain error InvalidCredentials so the API
layer maps them uniformly to a 401 problem-details response.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import jwt

from polsia.core.shared.errors import InvalidCredentials
from polsia.core.shared.ids import UserId
from polsia.infra.settings import Settings

ACCESS_TYP = "access"
REFRESH_TYP = "refresh"


@dataclasses.dataclass(frozen=True)
class Claims:
    """Verified token claims."""

    sub: UserId
    exp: int
    iat: int
    typ: str


class PyJWTTokenIssuer:
    """Issues and verifies HS256 (configurable) JWTs."""

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret
        self._algorithm = settings.jwt_algorithm
        self._access_ttl = dt.timedelta(minutes=settings.access_token_ttl_minutes)
        self._refresh_ttl = dt.timedelta(days=settings.refresh_token_ttl_days)

    # -- issuing ---------------------------------------------------------

    def issue_access_token(self, user_id: UserId) -> str:
        return self._issue(user_id, ACCESS_TYP, self._access_ttl)

    def issue_refresh_token(self, user_id: UserId) -> str:
        return self._issue(user_id, REFRESH_TYP, self._refresh_ttl)

    def _issue(self, user_id: UserId, typ: str, ttl: dt.timedelta) -> str:
        now = dt.datetime.now(dt.UTC)
        payload = {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
            "typ": typ,
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    # -- verification ------------------------------------------------------

    def verify_token(self, token: str, *, expected_typ: str | None = None) -> Claims:
        """Verify signature + expiry and return Claims.

        Raises InvalidCredentials on any failure (expired, bad signature,
        malformed payload, wrong typ when expected_typ is given).
        """
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except jwt.InvalidTokenError as exc:
            raise InvalidCredentials("invalid or expired token") from exc

        sub = payload.get("sub")
        typ = payload.get("typ")
        exp = payload.get("exp")
        iat = payload.get("iat")
        if not isinstance(sub, str) or not isinstance(typ, str):
            raise InvalidCredentials("malformed token payload")
        if expected_typ is not None and typ != expected_typ:
            raise InvalidCredentials("invalid or expired token")
        try:
            user_id = UserId.parse(sub)
        except ValueError as exc:
            raise InvalidCredentials("malformed token payload") from exc
        return Claims(
            sub=user_id,
            exp=int(exp) if exp is not None else 0,
            iat=int(iat) if iat is not None else 0,
            typ=typ,
        )
