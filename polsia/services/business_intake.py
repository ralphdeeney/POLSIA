"""BusinessIdeaService — the intake use case for submitted business ideas.

- Authorization (ADR-0002): every operation takes an explicit organization
  scope and verifies the actor's membership via MembershipRepository.
- ``raw_idea`` is stored verbatim (exactly the user-provided, untrusted text);
  anything derived during intake lives separately in ``inferred_requirements``
  — user text and inference are never merged.
- Idempotency: an explicit, caller-supplied idempotency key makes repeated
  identical submissions safe. The key and a payload fingerprint are persisted
  with the idea; repeats of the same key+payload reuse the idea, a same-key
  different-payload call is a conflict.
- Timeline: RECEIVED -> VALIDATED -> BLUEPRINT_GENERATED (REJECTED is the
  explicit early-validation failure path). Multiple independent ideas per
  organization are supported.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from polsia.core.business.entities import BusinessIdea
from polsia.core.business.errors import BusinessIdeaNotFound, DuplicateBusinessIdea
from polsia.core.business.ports import BusinessIdeaRepository
from polsia.core.business.values import IntakeStatus
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import EventBus, EventEnvelope, EventStore, emit
from polsia.core.shared.ids import BusinessIdeaId, OrganizationId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.planning import _CompanyScope


def inferred_requirements(idea: BusinessIdea) -> dict[str, Any]:
    """Deterministic derivation of explicitly *inferred* requirements."""
    return {
        "has_target_market": idea.target_market is not None,
        "has_industry": idea.industry is not None,
        "has_geo_preference": bool(idea.geo_preferences),
        "has_budget_constraint": idea.budget_constraints is not None,
        "explicit_constraints": list(idea.additional_constraints),
    }


def idea_fingerprint(
    *,
    raw_idea: str,
    target_market: str | None,
    industry: str | None,
    geo_preferences: list[str],
    budget_constraints: str | None,
    additional_constraints: list[str],
) -> str:
    """Stable content hash used for idempotency conflict detection."""
    payload = json.dumps(
        {
            "raw": raw_idea,
            "market": target_market,
            "industry": industry,
            "geo": sorted(geo_preferences),
            "budget": budget_constraints,
            "additional": sorted(additional_constraints),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BusinessIdeaService:
    """Intake use cases within an explicitly scoped organization."""

    def __init__(
        self,
        ideas: BusinessIdeaRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        store: EventStore | None = None,
        *,
        max_idea_length: int = 8_000,
    ) -> None:
        self._ideas = ideas
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus
        self._store = store
        self._max_length = max_idea_length

    # -- commands ---------------------------------------------------------

    async def submit_idea(
        self,
        actor: User,
        organization_id: OrganizationId,
        *,
        raw_idea: str,
        target_market: str | None = None,
        industry: str | None = None,
        geo_preferences: list[str] | None = None,
        budget_constraints: str | None = None,
        additional_constraints: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> BusinessIdea:
        """Create (or idempotently reuse) a business idea in VALIDATED status."""
        await self._scope.require_member(actor, organization_id)
        if not isinstance(raw_idea, str) or not raw_idea.strip():
            raise ValidationError("business idea must be a non-empty string")
        if len(raw_idea) > self._max_length:
            raise ValidationError(
                f"business idea exceeds maximum length ({len(raw_idea)} > {self._max_length})"
            )
        idea = await self._persist(
            organization_id,
            actor,
            raw_idea=raw_idea,
            target_market=target_market,
            industry=industry,
            geo_preferences=list(geo_preferences or []),
            budget_constraints=budget_constraints,
            additional_constraints=list(additional_constraints or []),
            idempotency_key=idempotency_key,
        )
        if not idea.created_by_this_call:
            await self._publish(
                "business_idea.reused",
                organization_id=organization_id,
                idea_id=idea.id,
                payload={"intake_status": idea.intake_status.value},
            )
            return idea
        await self._publish(
            "business_idea.submitted",
            organization_id=organization_id,
            idea_id=idea.id,
            payload={"intake_status": idea.intake_status.value},
        )
        return idea

    # -- queries ----------------------------------------------------------

    async def get_idea(
        self, actor: User, organization_id: OrganizationId, idea_id: BusinessIdeaId
    ) -> BusinessIdea:
        """Tenant-scoped fetch; a wrong-organization idea id is not found."""
        await self._scope.require_member(actor, organization_id)
        return await self._get(organization_id, idea_id)

    async def list_ideas(
        self, actor: User, organization_id: OrganizationId, *, limit: int = 100
    ) -> list[BusinessIdea]:
        await self._scope.require_member(actor, organization_id)
        return await self._ideas.list_for_organization(organization_id, limit=limit)

    async def validate_idea(
        self, actor: User, organization_id: OrganizationId, idea_id: BusinessIdeaId
    ) -> BusinessIdea:
        """Re-run the intake validation lifecycle step (idempotent)."""
        await self._scope.require_member(actor, organization_id)
        idea = await self._get(organization_id, idea_id)
        if idea.intake_status is not IntakeStatus.RECEIVED:
            return idea
        updated = idea.mark_validated()
        await self._ideas.update(updated)
        await self._publish(
            "business_idea.validated",
            organization_id=organization_id,
            idea_id=idea.id,
            payload={"intake_status": updated.intake_status.value},
        )
        return updated

    async def reject_idea(
        self,
        actor: User,
        organization_id: OrganizationId,
        idea_id: BusinessIdeaId,
        *,
        reason: str | None = None,
    ) -> BusinessIdea:
        """Mark the idea REJECTED (e.g. explicit withdrawal)."""
        await self._scope.require_member(actor, organization_id)
        idea = await self._get(organization_id, idea_id)
        if idea.intake_status is IntakeStatus.BLUEPRINT_GENERATED:
            raise ValidationError(
                "a blueprint-generated idea cannot be rejected without regenerating first"
            )
        updated = idea.mark_rejected()
        await self._ideas.update(updated)
        await self._publish(
            "business_idea.rejected",
            organization_id=organization_id,
            idea_id=idea.id,
            payload={"intake_status": updated.intake_status.value, "reason": reason},
        )
        return updated

    async def attach_blueprint_entity(
        self, idea: BusinessIdea, blueprint: dict[str, Any]
    ) -> BusinessIdea:
        """Attach a generated blueprint payload and advance the lifecycle."""
        updated = idea.attach_blueprint(blueprint)
        await self._ideas.update(updated)
        await self._publish(
            "business_idea.blueprint_attached",
            organization_id=idea.organization_id,
            idea_id=idea.id,
            payload={
                "blueprint_version": updated.blueprint_version,
                "blueprint_status": updated.blueprint_status.value,
            },
        )
        return updated

    # -- internals ---------------------------------------------------------

    async def _persist(
        self,
        organization_id: OrganizationId,
        actor: User,
        *,
        raw_idea: str,
        target_market: str | None,
        industry: str | None,
        geo_preferences: list[str],
        budget_constraints: str | None,
        additional_constraints: list[str],
        idempotency_key: str | None,
    ) -> BusinessIdea:
        if idempotency_key:
            fingerprint = idea_fingerprint(
                raw_idea=raw_idea,
                target_market=target_market,
                industry=industry,
                geo_preferences=geo_preferences,
                budget_constraints=budget_constraints,
                additional_constraints=additional_constraints,
            )
            existing = await self._ideas.find_by_idempotency_key(organization_id, idempotency_key)
            if existing is not None:
                persisted = (existing.inferred_requirements or {}).get("idempotency") or {}
                if persisted.get("fingerprint") != fingerprint:
                    raise DuplicateBusinessIdea(
                        f"idempotency key {idempotency_key!r} was already used for "
                        "a different idea payload"
                    )
                # Reuse: identical under the same key returns the existing idea.
                reused = BusinessIdea(
                    id=existing.id,
                    organization_id=existing.organization_id,
                    owner_user_id=existing.owner_user_id,
                    raw_idea=existing.raw_idea,
                    description=existing.description,
                    target_market=existing.target_market,
                    industry=existing.industry,
                    geo_preferences=list(existing.geo_preferences),
                    budget_constraints=existing.budget_constraints,
                    additional_constraints=list(existing.additional_constraints),
                    inferred_requirements=dict(existing.inferred_requirements or {}),
                    submitted_at=existing.submitted_at,
                    intake_status=existing.intake_status,
                    blueprint_status=existing.blueprint_status,
                    blueprint=existing.blueprint,
                    blueprint_version=existing.blueprint_version,
                    created_by_this_call=False,
                )
                return reused
        idea = BusinessIdea.submit(
            organization_id=organization_id,
            owner_user_id=actor.id,
            raw_idea=raw_idea,
            target_market=target_market,
            industry=industry,
            geo_preferences=geo_preferences,
            budget_constraints=budget_constraints,
            additional_constraints=additional_constraints,
        )
        idea = idea.mark_validated()
        requirements = inferred_requirements(idea)
        if idempotency_key:
            requirements["idempotency"] = {
                "key": idempotency_key,
                "fingerprint": idea_fingerprint(
                    raw_idea=raw_idea,
                    target_market=target_market,
                    industry=industry,
                    geo_preferences=geo_preferences,
                    budget_constraints=budget_constraints,
                    additional_constraints=additional_constraints,
                ),
            }
        idea = dataclasses_replace(idea, inferred_requirements=requirements)
        await self._ideas.add(idea)
        return dataclasses_replace(idea, created_by_this_call=True)

    async def _get(self, organization_id: OrganizationId, idea_id: BusinessIdeaId) -> BusinessIdea:
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


def dataclasses_replace(entity: BusinessIdea, **changes: Any) -> BusinessIdea:
    """Replacement helper so services never poke dataclass internals directly."""
    import dataclasses as _dc

    return _dc.replace(entity, **changes)
