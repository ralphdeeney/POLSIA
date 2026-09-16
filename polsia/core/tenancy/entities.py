"""Tenancy domain entities. Pure dataclasses + invariants; no I/O, no ORM."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import typing

from polsia.core.shared.errors import (
    DomainRuleError,
    InvalidStatusTransition,
    ValidationError,
)
from polsia.core.shared.events import utcnow
from polsia.core.shared.ids import CompanyId, MembershipId, OrganizationId, UserId
from polsia.core.tenancy.values import (
    COMPANY_STATUS_TRANSITIONS,
    CompanyStatus,
    MembershipRole,
    OrganizationStatus,
    UserStatus,
    validate_email,
    validate_slug,
)


@dataclasses.dataclass(frozen=True)
class User:
    id: UserId
    email: str
    hashed_password: str
    display_name: str
    status: UserStatus = UserStatus.ACTIVE
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def register(
        *, email: str, hashed_password: str, display_name: str, now: dt.datetime | None = None
    ) -> User:
        normalized = validate_email(email)
        if not hashed_password:
            raise ValidationError("hashed_password must not be empty")
        if not display_name.strip():
            raise ValidationError("display_name must not be empty")
        ts = now or utcnow()
        return User(
            id=UserId.generate(),
            email=normalized,
            hashed_password=hashed_password,
            display_name=display_name.strip(),
            status=UserStatus.ACTIVE,
            created_at=ts,
            updated_at=ts,
        )

    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE


@dataclasses.dataclass(frozen=True)
class Organization:
    id: OrganizationId
    name: str
    slug: str
    owner_user_id: UserId
    status: OrganizationStatus = OrganizationStatus.ACTIVE
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *, name: str, slug: str, owner_user_id: UserId, now: dt.datetime | None = None
    ) -> Organization:
        if not name.strip():
            raise ValidationError("organization name must not be empty")
        validate_slug(slug)
        ts = now or utcnow()
        return Organization(
            id=OrganizationId.generate(),
            name=name.strip(),
            slug=slug,
            owner_user_id=owner_user_id,
            status=OrganizationStatus.ACTIVE,
            created_at=ts,
            updated_at=ts,
        )

    def rename(self, name: str) -> Organization:
        if not name.strip():
            raise ValidationError("organization name must not be empty")
        return dataclasses.replace(self, name=name.strip())

    def archive(self) -> Organization:
        if self.status is OrganizationStatus.ARCHIVED:
            raise DomainRuleError("organization is already archived")
        return dataclasses.replace(self, status=OrganizationStatus.ARCHIVED)


def _loads_json(value: str | None) -> dict[str, typing.Any]:
    if not value:
        return {}
    return typing.cast(dict[str, typing.Any], json.loads(value))


@dataclasses.dataclass(frozen=True)
class Company:
    """A business instance. Business-agnostic: identity/mission/strategy/
    configuration are JSON payloads owned by the company instance, never
    interpreted by the OS."""

    id: CompanyId
    organization_id: OrganizationId
    name: str
    slug: str
    identity: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    mission: str | None = None
    strategy: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    configuration: dict[str, typing.Any] = dataclasses.field(default_factory=dict)
    status: CompanyStatus = CompanyStatus.DRAFT
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        organization_id: OrganizationId,
        name: str,
        slug: str,
        identity: dict[str, typing.Any] | None = None,
        mission: str | None = None,
        now: dt.datetime | None = None,
    ) -> Company:
        if not name.strip():
            raise ValidationError("company name must not be empty")
        validate_slug(slug)
        ts = now or utcnow()
        return Company(
            id=CompanyId.generate(),
            organization_id=organization_id,
            name=name.strip(),
            slug=slug,
            identity=dict(identity or {}),
            mission=mission,
            status=CompanyStatus.DRAFT,
            created_at=ts,
            updated_at=ts,
        )

    def _mutated(self, **changes: typing.Any) -> Company:
        return dataclasses.replace(self, updated_at=utcnow(), **changes)

    def rename(self, name: str) -> Company:
        if not name.strip():
            raise ValidationError("company name must not be empty")
        return self._mutated(name=name.strip())

    def transition_status(self, new_status: CompanyStatus) -> Company:
        allowed = COMPANY_STATUS_TRANSITIONS[self.status]
        if new_status not in allowed:
            raise InvalidStatusTransition(
                f"company {self.status.value!r} cannot transition to {new_status.value!r}"
            )
        return self._mutated(status=new_status)

    def update_identity(self, identity: dict[str, typing.Any]) -> Company:
        return self._mutated(identity=dict(identity))

    def update_mission(self, mission: str | None) -> Company:
        return self._mutated(mission=mission)

    def update_strategy(self, strategy: dict[str, typing.Any]) -> Company:
        return self._mutated(strategy=dict(strategy))

    def update_configuration(self, configuration: dict[str, typing.Any]) -> Company:
        return self._mutated(configuration=dict(configuration))


@dataclasses.dataclass(frozen=True)
class OrgMembership:
    id: MembershipId
    organization_id: OrganizationId
    user_id: UserId
    role: MembershipRole
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        organization_id: OrganizationId,
        user_id: UserId,
        role: MembershipRole,
    ) -> OrgMembership:
        return OrgMembership(
            id=MembershipId.generate(),
            organization_id=organization_id,
            user_id=user_id,
            role=role,
        )
