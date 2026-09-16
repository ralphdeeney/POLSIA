"""Security adapter tests: bcrypt hashing and PyJWT token issue/verify."""

from __future__ import annotations

import datetime as dt

import jwt as pyjwt
import pytest
from polsia.adapters.security.passwords import BcryptPasswordHasher
from polsia.adapters.security.tokens import ACCESS_TYP, REFRESH_TYP, PyJWTTokenIssuer
from polsia.core.shared.errors import InvalidCredentials
from polsia.core.shared.ids import UserId
from polsia.infra.settings import Settings


class TestBcryptPasswordHasher:
    def test_hash_verify_roundtrip(self) -> None:
        hasher = BcryptPasswordHasher()
        hashed = hasher.hash("correct horse battery staple")
        assert hashed != "correct horse battery staple"
        assert hashed.startswith("$2b$12$")
        assert hasher.verify("correct horse battery staple", hashed) is True

    def test_wrong_password_fails(self) -> None:
        hasher = BcryptPasswordHasher()
        hashed = hasher.hash("s3cret-password")
        assert hasher.verify("wrong-password", hashed) is False

    def test_hashes_are_salted(self) -> None:
        hasher = BcryptPasswordHasher()
        assert hasher.hash("same") != hasher.hash("same")

    def test_verify_malformed_hash_is_false_not_error(self) -> None:
        hasher = BcryptPasswordHasher()
        assert hasher.verify("pw", "not-a-bcrypt-hash") is False


def _issuer(**overrides: object) -> PyJWTTokenIssuer:
    kwargs: dict[str, object] = {
        "jwt_secret": "0123456789abcdef0123456789abcdef",
        "jwt_algorithm": "HS256",
        "access_token_ttl_minutes": 30,
        "refresh_token_ttl_days": 14,
        **overrides,
    }
    return PyJWTTokenIssuer(Settings(**kwargs))  # type: ignore[arg-type]


class TestPyJWTTokenIssuer:
    def test_access_token_issue_verify_roundtrip(self) -> None:
        issuer = _issuer()
        user_id = UserId.generate()
        token = issuer.issue_access_token(user_id)

        claims = issuer.verify_token(token)
        assert claims.sub == user_id
        assert claims.typ == ACCESS_TYP
        assert claims.exp > claims.iat

    def test_refresh_token_roundtrip(self) -> None:
        issuer = _issuer()
        user_id = UserId.generate()
        claims = issuer.verify_token(issuer.issue_refresh_token(user_id))
        assert claims.sub == user_id
        assert claims.typ == REFRESH_TYP

    def test_expired_token_rejected(self) -> None:
        issuer = _issuer()
        user_id = UserId.generate()
        now = int(dt.datetime.now(dt.UTC).timestamp())
        expired = pyjwt.encode(
            {"sub": str(user_id), "iat": now - 3600, "exp": now - 60, "typ": ACCESS_TYP},
            "0123456789abcdef0123456789abcdef",
            algorithm="HS256",
        )
        with pytest.raises(InvalidCredentials):
            issuer.verify_token(expired)

    def test_tampered_token_rejected(self) -> None:
        issuer = _issuer()
        token = issuer.issue_access_token(UserId.generate())
        header, payload, signature = token.split(".")
        tampered = f"{header}.{payload}.AAA{signature[3:]}"
        with pytest.raises(InvalidCredentials):
            issuer.verify_token(tampered)

    def test_wrong_secret_rejected(self) -> None:
        issuer = _issuer()
        other = _issuer(jwt_secret="fedcba9876543210fedcba9876543210")
        token = issuer.issue_access_token(UserId.generate())
        with pytest.raises(InvalidCredentials):
            other.verify_token(token)

    def test_malformed_token_rejected(self) -> None:
        with pytest.raises(InvalidCredentials):
            _issuer().verify_token("garbage.token")

    def test_wrong_typ_rejected_when_expected(self) -> None:
        issuer = _issuer()
        refresh = issuer.issue_refresh_token(UserId.generate())
        with pytest.raises(InvalidCredentials):
            issuer.verify_token(refresh, expected_typ=ACCESS_TYP)
