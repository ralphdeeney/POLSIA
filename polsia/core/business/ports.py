"""Ports for the business domain. Business-agnostic and adapter-free."""

from __future__ import annotations

from typing import Any, Protocol

from polsia.core.business.entities import BusinessIdea, CompanyBlueprint
from polsia.core.shared.ids import BusinessIdeaId, OrganizationId


class BusinessIdeaRepository(Protocol):
    """Persistence port for submitted business ideas (tenant-scoped)."""

    async def add(self, idea: BusinessIdea) -> None: ...

    async def get(
        self, organization_id: OrganizationId, idea_id: BusinessIdeaId
    ) -> BusinessIdea | None: ...

    async def update(self, idea: BusinessIdea) -> None: ...

    async def list_for_organization(
        self, organization_id: OrganizationId, *, limit: int = 100
    ) -> list[BusinessIdea]: ...

    async def find_by_idempotency_key(
        self, organization_id: OrganizationId, idempotency_key: str
    ) -> BusinessIdea | None: ...


class BlueprintPort(Protocol):
    """Provider-neutral blueprint generation seam.

    Implementations: :class:`DeterministicBlueprintGenerator` (offline,
    contract tests, CI) and :class:`ModelBackedBlueprintGenerator` (uses the
    Phase 4 ModelPort). A model-generated blueprint is a proposal until it
    passes validation and is persisted through the existing services.
    """

    name: str

    async def generate(
        self, idea: BusinessIdea, *, context: dict[str, Any] | None = None
    ) -> CompanyBlueprint: ...
