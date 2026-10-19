"""BlueprintService — generation, validation and persistence of blueprints.

A model-generated blueprint is a *proposal* until it passes
``CompanyBlueprint.validate()`` and is durably persisted through the idea
lifecycle (the existing idea record owns the state — no second business state
system). Generation uses the injected :class:`BlueprintPort`
(deterministic by default, model-backed when wired by the composition root).
"""

from __future__ import annotations

from typing import Any

from polsia.core.business.entities import (
    BusinessIdea,
    CompanyBlueprint,
    blueprint_from_dict,
)
from polsia.core.business.errors import (
    BlueprintNotValidated,
    BlueprintValidationError,
    BusinessIdeaNotFound,
)
from polsia.core.business.ports import BlueprintPort, BusinessIdeaRepository
from polsia.core.business.values import BlueprintStatus
from polsia.core.shared.events import EventBus, EventEnvelope, EventStore, emit
from polsia.core.shared.ids import BusinessIdeaId, OrganizationId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.business_intake import BusinessIdeaService
from polsia.services.planning import _CompanyScope
from polsia.services.verification import ArtifactVerifier


class BlueprintService:
    """Blueprint use cases within an explicitly scoped organization."""

    def __init__(
        self,
        ideas: BusinessIdeaRepository,
        generator: BlueprintPort,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        store: EventStore | None = None,
        verifier: ArtifactVerifier | None = None,
    ) -> None:
        self._ideas = ideas
        self._generator = generator
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus
        self._store = store
        self._verifier = verifier or ArtifactVerifier()

    # -- commands ---------------------------------------------------------

    async def generate_blueprint(
        self,
        actor: User,
        organization_id: OrganizationId,
        idea_id: BusinessIdeaId,
        idea_service: BusinessIdeaService,
        *,
        context: dict[str, Any] | None = None,
    ) -> BusinessIdea:
        """Generate, validate and persist a blueprint for a submitted idea.

        The generated artifact is verified (schema, sections, consistency,
        ownership, provenance, persistence). Invalid artifacts reject the
        lifecycle step without breaking out of the idea's tenant state. An
        already-generated idea re-attaches a fresh version (re-generation is
        an explicit idempotent update of the payload).
        """
        await self._scope.require_member(actor, organization_id)
        idea = await self._get_idea(organization_id, idea_id)
        generator = self._generator
        blueprint = await generator.generate(idea, context=context)
        return await self._persist_blueprint(organization_id, idea, blueprint, idea_service)

    async def validate_blueprint(
        self,
        actor: User,
        organization_id: OrganizationId,
        idea_id: BusinessIdeaId,
    ) -> BusinessIdea:
        """Re-validate the persisted blueprint; durable lifecycle update only."""
        await self._scope.require_member(actor, organization_id)
        idea = await self._get_idea(organization_id, idea_id)
        if idea.blueprint is None:
            raise BlueprintNotValidated(
                f"business idea {idea_id} has no generated blueprint to validate"
            )
        blueprint = self._load_blueprint(idea)
        outcome = self._verifier.verify_blueprint_artifact(blueprint, persisted=idea)
        if not outcome.passed:
            raise BlueprintValidationError(outcome.reason)
        if idea.blueprint_status is not BlueprintStatus.VALIDATED:
            updated = idea.mark_blueprint_validated()
            await self._ideas.update(updated)
            await self._publish(
                "business_blueprint.validated",
                organization_id=organization_id,
                idea_id=idea.id,
                payload={"blueprint_status": updated.blueprint_status.value},
            )
            return updated
        return idea

    # -- queries ----------------------------------------------------------

    async def get_blueprint(
        self, actor: User, organization_id: OrganizationId, idea_id: BusinessIdeaId
    ) -> dict[str, Any]:
        """Return the persisted blueprint payload (tenant-scoped)."""
        await self._scope.require_member(actor, organization_id)
        idea = await self._get_idea(organization_id, idea_id)
        if idea.blueprint is None:
            raise BlueprintNotValidated(f"business idea {idea_id} has no generated blueprint yet")
        return dict(idea.blueprint)

    # -- internals --------------------------------------------------------

    async def _persist_blueprint(
        self,
        organization_id: OrganizationId,
        idea: BusinessIdea,
        blueprint: CompanyBlueprint,
        idea_service: BusinessIdeaService,
    ) -> BusinessIdea:
        """Validate + verify + attach a generated proposal (all deterministic)."""

        blueprint.validate()
        # Persist via the intake service (single owner of idea state), then
        # reload so verification sees the exactly-persisted payload.
        updated = await idea_service.attach_blueprint_entity(idea, blueprint.to_dict())
        persisted = await self._get_idea(organization_id, updated.id)
        outcome = self._verifier.verify_blueprint_artifact(blueprint, persisted=persisted)
        if not outcome.passed:
            raise BlueprintValidationError(outcome.reason)
        validated = persisted.mark_blueprint_validated()
        await self._ideas.update(validated)
        await self._publish(
            "business_blueprint.generated",
            organization_id=organization_id,
            idea_id=idea.id,
            payload={
                "source": blueprint.source.value,
                "business_model": blueprint.business_model.value,
                "research_used": blueprint.research_used,
                "blueprint_status": validated.blueprint_status.value,
            },
        )
        return validated

    def _load_blueprint(self, idea: BusinessIdea) -> CompanyBlueprint:
        data = idea.blueprint
        if not isinstance(data, dict):
            raise BlueprintValidationError("persisted blueprint payload is not a JSON object")
        return blueprint_from_dict(idea.id, idea.organization_id, data)

    async def _get_idea(
        self, organization_id: OrganizationId, idea_id: BusinessIdeaId
    ) -> BusinessIdea:
        idea = await self._ideas.get(organization_id, idea_id)
        if idea is None:
            raise BusinessIdeaNotFound(f"business idea {idea_id} not found")
        return idea

    async def _publish(
        self,
        event_type: str,
        *,
        organization_id: OrganizationId,
        idea_id: BusinessIdeaId,
        payload: dict[str, Any],
    ) -> None:
        await emit(
            self._bus,
            self._store,
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                payload={"business_idea_id": str(idea_id), **payload},
            ),
        )
