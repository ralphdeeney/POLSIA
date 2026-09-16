"""Domain error taxonomy.

Domain errors are raised by core entities/services; the API layer translates
them into RFC 9457 problem-details responses. Adapters translate their own
infrastructure errors into these before crossing the boundary.
"""

from __future__ import annotations


class PolsiaError(Exception):
    """Base class for all Polsia errors."""

    #: HTTP status the API layer should map this error to.
    status_code = 500
    #: Stable machine-readable error code.
    code = "internal_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.__class__.__name__)


class ValidationError(PolsiaError):
    status_code = 422
    code = "validation_error"


class InvalidSlug(ValidationError):
    code = "invalid_slug"


class ConflictError(PolsiaError):
    status_code = 409
    code = "conflict"


class DuplicateEmail(ConflictError):
    code = "duplicate_email"


class DuplicateSlug(ConflictError):
    code = "duplicate_slug"


class DuplicateMembership(ConflictError):
    code = "duplicate_membership"


class NotFoundError(PolsiaError):
    status_code = 404
    code = "not_found"


class UserNotFoundError(NotFoundError):
    code = "user_not_found"


class OrganizationNotFoundError(NotFoundError):
    code = "organization_not_found"


class CompanyNotFoundError(NotFoundError):
    code = "company_not_found"


class AuthorizationError(PolsiaError):
    status_code = 403
    code = "forbidden"


class NotAMember(AuthorizationError):
    code = "not_a_member"


class InsufficientRole(AuthorizationError):
    code = "insufficient_role"


class InvalidCredentials(PolsiaError):
    status_code = 401
    code = "invalid_credentials"


class DomainRuleError(PolsiaError):
    """A domain invariant was violated by an attempted transition."""

    status_code = 422
    code = "domain_rule_error"


class InvalidStatusTransition(DomainRuleError):
    code = "invalid_status_transition"
