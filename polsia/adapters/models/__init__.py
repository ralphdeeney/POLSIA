"""Real model provider adapters (Phase 4)."""

from __future__ import annotations

from polsia.adapters.models.errors import (
    ModelProviderError,
    ProviderAuthError,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
)
from polsia.adapters.models.openai_compatible import (
    ModelPricing,
    OpenAICompatibleConfig,
    OpenAICompatibleModelProvider,
    ProviderCapabilityReport,
)

__all__ = [
    "ModelPricing",
    "ModelProviderError",
    "OpenAICompatibleConfig",
    "OpenAICompatibleModelProvider",
    "ProviderAuthError",
    "ProviderCapabilityReport",
    "ProviderRateLimited",
    "ProviderResponseError",
    "ProviderTimeout",
    "ProviderUnavailable",
]
