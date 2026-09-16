"""Translate adapter-level failures into domain errors.

Unique-constraint violations are identified by inspecting the constraint and
column/table names that appear in the driver message (both SQLite and
PostgreSQL include the constraint name or the offending table.column pair).
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from polsia.core.shared.errors import (
    ConflictError,
    DuplicateEmail,
    DuplicateMembership,
    DuplicateSlug,
)


def _message(exc: IntegrityError) -> str:
    orig = str(getattr(exc, "orig", "") or "")
    return f"{exc} {orig}".lower()


def translate_integrity_error(exc: IntegrityError) -> ConflictError:
    """Map an IntegrityError to the most specific domain conflict error."""
    msg = _message(exc)

    # users.email (uq_users_email / "users.email")
    if "uq_users_email" in msg or ("users" in msg and "email" in msg):
        return DuplicateEmail("a user with this email already exists")

    # org_memberships (organization_id, user_id)
    if "uq_org_memberships" in msg or "org_memberships" in msg:
        return DuplicateMembership("this user is already a member of the organization")

    # companies (organization_id, slug) — checked before plain "slug" so the
    # per-organization constraint is distinguishable from organizations.slug.
    if "companies" in msg and "slug" in msg:
        return DuplicateSlug("a company with this slug already exists in this organization")

    # organizations.slug (uq_organizations_slug / "organizations.slug")
    if "uq_organizations_slug" in msg or ("organizations" in msg and "slug" in msg):
        return DuplicateSlug("an organization with this slug already exists")

    return ConflictError("resource already exists")
