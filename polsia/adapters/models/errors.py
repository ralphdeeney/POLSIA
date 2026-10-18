"""Model provider adapter errors (normalized, tenant-agnostic)."""

from __future__ import annotations

from polsia.core.shared.errors import PolsiaError


class ModelProviderError(PolsiaError):
    """Base class for normalized provider failures."""


class ProviderAuthError(ModelProviderError):
    """The provider rejected credentials (401/403)."""


class ProviderRateLimited(ModelProviderError):
    """The provider rate-limited the request (429)."""


class ProviderUnavailable(ModelProviderError):
    """The provider is temporarily unreachable or erroring (5xx/connect)."""


class ProviderResponseError(ModelProviderError):
    """The provider answered but the payload could not be normalized."""


class ProviderTimeout(TimeoutError):
    """The request exceeded its per-attempt timeout."""
