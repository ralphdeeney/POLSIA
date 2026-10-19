"""Business blueprint generation errors (normalized, provider-agnostic)."""

from __future__ import annotations

from polsia.adapters.models.errors import ModelProviderError
from polsia.core.business.errors import BlueprintValidationError


class BlueprintGenerationError(ModelProviderError):
    """A model-backed blueprint generation pipeline could not produce a valid
    blueprint within its budget. The idea is left untouched; callers retry or
    fall back to the deterministic generator."""


class BlueprintSchemaError(BlueprintGenerationError):
    """The model returned malformed JSON or an unparsable blueprint payload."""


class BlueprintBudgetExceeded(BlueprintGenerationError):
    """Token/time/call budget exhausted before a valid blueprint was produced."""


__all__ = [
    "BlueprintGenerationError",
    "BlueprintSchemaError",
    "BlueprintBudgetExceeded",
    "BlueprintValidationError",
]
