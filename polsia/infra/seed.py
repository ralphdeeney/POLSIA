"""Local development seed: creates a demo user, organization and company.

Run: python -m polsia.infra.seed   (uses POLSIA_DATABASE_URL; on the default
in-memory SQLite the data is transient — set a file URL or Postgres to keep it).
"""

from __future__ import annotations

import asyncio

from polsia.adapters.persistence.repositories import (
    SqlAlchemyCompanyRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from polsia.adapters.security.passwords import BcryptPasswordHasher
from polsia.core.auth.service import AuthService, TokenClaims
from polsia.core.shared.events import EventBus
from polsia.core.shared.ids import UserId
from polsia.core.tenancy.values import CompanyStatus
from polsia.infra.db import create_all_metadata, make_engine, make_session_factory
from polsia.services.tenancy import CompanyService, OrganizationService


async def seed() -> None:
    from polsia.adapters.events import InProcessEventBus, LoggingSubscriber

    bus: EventBus = InProcessEventBus()
    bus.subscribe("*", LoggingSubscriber())

    engine = make_engine()
    factory = make_session_factory(engine)
    await create_all_metadata(engine)

    hasher = BcryptPasswordHasher()
    async with factory() as session:
        auth = AuthService(SqlAlchemyUserRepository(session), hasher, _NullTokens())
        users = SqlAlchemyUserRepository(session)
        orgs = SqlAlchemyOrganizationRepository(session)
        memberships = SqlAlchemyMembershipRepository(session)
        companies = SqlAlchemyCompanyRepository(session)

        user = await auth.register(
            email="founder@demo.local", password="demo-password-123", display_name="Demo Founder"
        )
        org_service = OrganizationService(orgs, memberships, bus, users=users)
        org = await org_service.create_organization(user, name="Demo Organization", slug="demo-org")
        company_service = CompanyService(companies, memberships, bus)
        company = await company_service.create_company(
            user,
            org.id,
            name="Demo Company",
            slug="demo-company",
            identity={"kind": "placeholder", "description": "Replace via PATCH /companies"},
            mission="A placeholder company instance running on the Polsia OS.",
        )
        company = await company_service.change_status(
            user, org.id, company.id, new_status=CompanyStatus.ACTIVE
        )
        print(f"user:     {user.email}")
        print(f"org:      {org.name} ({org.id})")
        print(f"company:  {company.name} ({company.id}) status={company.status.value}")
    await engine.dispose()


class _NullTokens:
    """Seed does not issue JWTs; satisfies the AuthService TokenIssuer port."""

    def issue_access_token(self, user_id: UserId) -> str:  # noqa: ARG002
        return ""

    def issue_refresh_token(self, user_id: UserId) -> str:  # noqa: ARG002
        return ""

    def verify_token(self, token: str) -> TokenClaims:  # noqa: ARG002
        from polsia.core.shared.errors import InvalidCredentials

        raise InvalidCredentials("seed runtime does not verify tokens")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
