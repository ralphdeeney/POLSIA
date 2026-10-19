"""SQLAlchemy repository for tenant-scoped business ideas.

Flush-only; the session provider owns the transaction boundary. Every lookup
takes the explicit organization scope so cross-tenant access is structurally
impossible (the organization id is part of the key).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.business_models import BusinessIdeaModel
from polsia.adapters.persistence.models import OrganizationModel, UserModel
from polsia.core.business.entities import BusinessIdea
from polsia.core.business.values import BlueprintStatus, IntakeStatus
from polsia.core.shared.errors import OrganizationNotFoundError, UserNotFoundError
from polsia.core.shared.ids import BusinessIdeaId, OrganizationId, UserId


def _aware(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _to_idea(row: BusinessIdeaModel) -> BusinessIdea:
    return BusinessIdea(
        id=BusinessIdeaId.parse(row.id),
        organization_id=OrganizationId.parse(row.organization_id),
        owner_user_id=UserId.parse(row.owner_user_id),
        raw_idea=row.raw_idea,
        description=row.description,
        target_market=row.target_market,
        industry=row.industry,
        geo_preferences=_strings(row.geo_preferences),
        budget_constraints=row.budget_constraints,
        additional_constraints=_strings(row.additional_constraints),
        inferred_requirements=dict(row.inferred_requirements or {}),
        submitted_at=_aware(row.submitted_at),
        intake_status=IntakeStatus(row.intake_status),
        blueprint_status=BlueprintStatus(row.blueprint_status),
        blueprint=dict(row.blueprint) if row.blueprint else None,
        blueprint_version=int(row.blueprint_version),
    )


class SqlAlchemyBusinessIdeaRepository:
    """Tenant-scoped CRUD for business ideas."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, idea: BusinessIdea) -> None:
        # Cross-tenant structural guard: the organization and owner must exist,
        # so an idea record can never silently outlive its tenant boundary.
        organization = await self._session.get(OrganizationModel, str(idea.organization_id))
        if organization is None:
            raise OrganizationNotFoundError(f"organization {idea.organization_id} not found")
        user = await self._session.get(UserModel, str(idea.owner_user_id))
        if user is None:
            raise UserNotFoundError(f"user {idea.owner_user_id} not found")
        self._session.add(self._row(idea))
        await self._session.flush()

    async def get(
        self, organization_id: OrganizationId, idea_id: BusinessIdeaId
    ) -> BusinessIdea | None:
        stmt = select(BusinessIdeaModel).where(
            BusinessIdeaModel.id == str(idea_id),
            BusinessIdeaModel.organization_id == str(organization_id),
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_idea(row) if row is not None else None

    async def update(self, idea: BusinessIdea) -> None:
        row = await self._load_row(idea.organization_id, idea.id)
        if row is None:
            raise LookupError(f"business idea {idea.id} not found in {idea.organization_id}")
        row.description = idea.description
        row.target_market = idea.target_market
        row.industry = idea.industry
        row.geo_preferences = list(idea.geo_preferences)
        row.budget_constraints = idea.budget_constraints
        row.additional_constraints = list(idea.additional_constraints)
        row.inferred_requirements = dict(idea.inferred_requirements or {})
        row.intake_status = idea.intake_status.value
        row.blueprint_status = idea.blueprint_status.value
        row.blueprint = dict(idea.blueprint) if idea.blueprint else None
        row.blueprint_version = int(idea.blueprint_version)
        row.updated_at = dt.datetime.now(dt.UTC)
        await self._session.flush()

    async def list_for_organization(
        self, organization_id: OrganizationId, *, limit: int = 100
    ) -> list[BusinessIdea]:
        stmt = (
            select(BusinessIdeaModel)
            .where(BusinessIdeaModel.organization_id == str(organization_id))
            .order_by(BusinessIdeaModel.submitted_at.desc(), BusinessIdeaModel.id.desc())
            .limit(max(1, min(int(limit), 1000)))
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_idea(row) for row in rows]

    async def find_by_idempotency_key(
        self, organization_id: OrganizationId, idempotency_key: str
    ) -> BusinessIdea | None:
        """Tenant-scoped idempotency lookup via the persisted key fingerprint."""
        stmt = (
            select(BusinessIdeaModel)
            .where(
                BusinessIdeaModel.organization_id == str(organization_id),
                BusinessIdeaModel.inferred_requirements["idempotency"]["key"].as_string()
                == idempotency_key,
            )
            .order_by(BusinessIdeaModel.submitted_at.desc())
            .limit(1)
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_idea(row) if row is not None else None

    async def _load_row(
        self, organization_id: OrganizationId, idea_id: BusinessIdeaId
    ) -> BusinessIdeaModel | None:
        stmt = select(BusinessIdeaModel).where(
            BusinessIdeaModel.id == str(idea_id),
            BusinessIdeaModel.organization_id == str(organization_id),
        )
        return (await self._session.scalars(stmt)).first()

    @staticmethod
    def _row(idea: BusinessIdea) -> BusinessIdeaModel:
        now = dt.datetime.now(dt.UTC)
        return BusinessIdeaModel(
            id=str(idea.id),
            organization_id=str(idea.organization_id),
            owner_user_id=str(idea.owner_user_id),
            raw_idea=idea.raw_idea,
            description=idea.description,
            target_market=idea.target_market,
            industry=idea.industry,
            geo_preferences=list(idea.geo_preferences),
            budget_constraints=idea.budget_constraints,
            additional_constraints=list(idea.additional_constraints),
            inferred_requirements=dict(idea.inferred_requirements or {}),
            intake_status=idea.intake_status.value,
            blueprint_status=idea.blueprint_status.value,
            blueprint=dict(idea.blueprint) if idea.blueprint else None,
            blueprint_version=int(idea.blueprint_version),
            submitted_at=idea.submitted_at,
            updated_at=now,
        )
