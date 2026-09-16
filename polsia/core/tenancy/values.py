"""Value objects for the tenancy domain."""

from __future__ import annotations

import enum
import re

from polsia.core.shared.errors import InvalidSlug

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_slug(slug: str) -> str:
    """Lowercase alphanumeric + hyphens, 2-63 chars, no leading/trailing hyphen."""
    if not SLUG_RE.match(slug):
        raise InvalidSlug(
            "slug must be 2-63 chars of lowercase letters, digits or hyphens, "
            "starting and ending with a letter or digit"
        )
    return slug


def validate_email(email: str) -> str:
    normalized = email.strip().lower()
    if not EMAIL_RE.match(normalized):
        raise ValueError("invalid email address")
    return normalized


class UserStatus(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class OrganizationStatus(enum.StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class CompanyStatus(enum.StrEnum):
    """Company lifecycle. Transitions are an explicit state machine (see below)."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


#: Allowed company status transitions. ARCHIVED is terminal.
COMPANY_STATUS_TRANSITIONS: dict[CompanyStatus, frozenset[CompanyStatus]] = {
    CompanyStatus.DRAFT: frozenset({CompanyStatus.ACTIVE, CompanyStatus.ARCHIVED}),
    CompanyStatus.ACTIVE: frozenset({CompanyStatus.PAUSED, CompanyStatus.ARCHIVED}),
    CompanyStatus.PAUSED: frozenset({CompanyStatus.ACTIVE, CompanyStatus.ARCHIVED}),
    CompanyStatus.ARCHIVED: frozenset(),
}


class MembershipRole(enum.StrEnum):
    """Organization role hierarchy. Rank grows with privilege."""

    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return {"member": 0, "admin": 1, "owner": 2}[self.value]

    def at_least(self, required: MembershipRole) -> bool:
        return self.rank >= required.rank
