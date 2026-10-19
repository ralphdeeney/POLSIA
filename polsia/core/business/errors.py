"""Domain errors for the business layer.

They subclass the shared error taxonomy so the API problem-details mapping
keeps working unchanged.
"""

from __future__ import annotations

from polsia.core.shared.errors import (
    ConflictError,
    DomainRuleError,
    NotFoundError,
    ValidationError,
)

__all__ = [
    "DuplicateBusinessIdea",
    "BusinessIdeaNotFound",
    "BlueprintValidationError",
    "BlueprintNotValidated",
    "BootstrapError",
    "BlueprintNotFound",
    "DuplicateCompanyBootstrap",
]


class DuplicateBusinessIdea(ConflictError):
    """Safe idempotent redelivery detected an existing idea."""

    code = "duplicate_business_idea"


class BusinessIdeaNotFound(NotFoundError):
    """Referenced business idea does not exist."""

    code = "business_idea_not_found"


class BlueprintValidationError(DomainRuleError):
    """Blueprint failed structural completeness / internal consistency."""

    code = "blueprint_validation_error"


class BlueprintNotValidated(ValidationError):
    """Bootstrap requires a validated blueprint."""

    code = "blueprint_not_validated"


class BootstrapError(DomainRuleError):
    """Company bootstrap failed or is only partially complete."""

    code = "bootstrap_error"


class BlueprintNotFound(NotFoundError):
    """Referenced blueprint does not exist."""

    code = "blueprint_not_found"


class DuplicateCompanyBootstrap(ConflictError):
    """Bootstrap for the same idea/blueprint already exists."""

    code = "duplicate_company_bootstrap"
