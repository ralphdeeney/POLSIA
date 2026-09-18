"""Planning application services: Goal and KPI use cases.

Company-scoped (ADR-0002): every operation takes an explicit organization
scope, verifies the actor's org membership, and verifies the target company
belongs to that organization. Events follow ADR-0004.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from polsia.core.planning.entities import Goal, Kpi
from polsia.core.planning.ports import GoalRepository, KpiRepository
from polsia.core.planning.values import GoalStatus, KpiDirection, Priority
from polsia.core.shared.errors import (
    CompanyNotFoundError,
    NotAMember,
    NotFoundError,
)
from polsia.core.shared.events import EventBus, EventEnvelope, EventStore, emit
from polsia.core.shared.ids import CompanyId, GoalId, KpiId, OrganizationId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository


class _CompanyScope:
    """Shared authorization helper for company-scoped Phase 2 services."""

    def __init__(self, companies: CompanyRepository, memberships: MembershipRepository) -> None:
        self._companies = companies
        self._memberships = memberships

    async def require_member(self, actor: User, organization_id: OrganizationId) -> None:
        if await self._memberships.get(organization_id, actor.id) is None:
            raise NotAMember(f"user {actor.id} is not a member of organization {organization_id}")

    async def require_company(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId
    ) -> None:
        await self.require_member(actor, organization_id)
        company = await self._companies.get(organization_id, company_id)
        if company is None:
            raise CompanyNotFoundError(
                f"company {company_id} not found in organization {organization_id}"
            )


class GoalService:
    """Goal use cases within an explicitly scoped organization + company."""

    def __init__(
        self,
        goals: GoalRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        store: EventStore | None = None,
    ) -> None:
        self._goals = goals
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus
        self._store = store

    async def create_goal(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        title: str,
        description: str | None = None,
        parent_goal_id: GoalId | None = None,
        priority: Priority = Priority.MEDIUM,
        due_at: dt.datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Goal:
        await self._scope.require_company(actor, organization_id, company_id)
        if parent_goal_id is not None:
            parent = await self._goals.get(company_id, parent_goal_id)
            if parent is None:
                raise NotFoundError(f"parent goal {parent_goal_id} not found")
        goal = Goal.create(
            company_id=company_id,
            title=title,
            description=description,
            parent_goal_id=parent_goal_id,
            priority=priority,
            due_at=due_at,
            metadata=metadata,
        )
        await self._goals.add(goal)
        await self._publish(
            "goal.created",
            organization_id=organization_id,
            company_id=company_id,
            goal_id=goal.id,
            payload={
                "title": goal.title,
                "status": goal.status.value,
                "priority": goal.priority.value,
            },
        )
        return goal

    async def get_goal(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId, goal_id: GoalId
    ) -> Goal:
        await self._scope.require_company(actor, organization_id, company_id)
        return await self._load(company_id, goal_id)

    async def list_goals(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId
    ) -> list[Goal]:
        await self._scope.require_company(actor, organization_id, company_id)
        return await self._goals.list_for_company(company_id)

    async def list_subgoals(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId, goal_id: GoalId
    ) -> list[Goal]:
        await self._scope.require_company(actor, organization_id, company_id)
        await self._load(company_id, goal_id)
        return await self._goals.list_for_goal(company_id, goal_id)

    async def update_goal(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        goal_id: GoalId,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: Priority | None = None,
        due_at: dt.datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Goal:
        await self._scope.require_company(actor, organization_id, company_id)
        goal = await self._load(company_id, goal_id)
        if title is not None:
            goal = goal.rename(title)
        goal = goal.update(
            description=description, priority=priority, due_at=due_at, metadata=metadata
        )
        await self._goals.update(goal)
        await self._publish(
            "goal.updated",
            organization_id=organization_id,
            company_id=company_id,
            goal_id=goal.id,
            payload={"title": goal.title, "status": goal.status.value},
        )
        return goal

    async def change_status(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        goal_id: GoalId,
        *,
        new_status: GoalStatus,
    ) -> Goal:
        await self._scope.require_company(actor, organization_id, company_id)
        goal = await self._load(company_id, goal_id)
        previous = goal.status
        goal = goal.transition_status(new_status)
        await self._goals.update(goal)
        await self._publish(
            "goal.status_changed",
            organization_id=organization_id,
            company_id=company_id,
            goal_id=goal.id,
            payload={"from": previous.value, "to": new_status.value},
        )
        if new_status is GoalStatus.COMPLETED:
            await self._publish(
                "goal.completed",
                organization_id=organization_id,
                company_id=company_id,
                goal_id=goal.id,
                payload={"title": goal.title},
            )
        elif new_status is GoalStatus.CANCELLED:
            await self._publish(
                "goal.cancelled",
                organization_id=organization_id,
                company_id=company_id,
                goal_id=goal.id,
                payload={"title": goal.title},
            )
        return goal

    async def _load(self, company_id: CompanyId, goal_id: GoalId) -> Goal:
        goal = await self._goals.get(company_id, goal_id)
        if goal is None:
            raise NotFoundError(f"goal {goal_id} not found")
        return goal

    async def _publish(
        self,
        event_type: str,
        *,
        organization_id: OrganizationId,
        company_id: CompanyId,
        goal_id: GoalId,
        payload: dict[str, Any],
    ) -> None:
        await emit(
            self._bus,
            self._store,
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload={"goal_id": str(goal_id), **payload},
            ),
        )


class KpiService:
    """KPI use cases. Status evaluation is deterministic (no AI)."""

    def __init__(
        self,
        kpis: KpiRepository,
        goals: GoalRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        store: EventStore | None = None,
    ) -> None:
        self._kpis = kpis
        self._goals = goals
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus
        self._store = store

    async def create_kpi(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        name: str,
        direction: KpiDirection,
        target_value: float,
        goal_id: GoalId | None = None,
        description: str | None = None,
        unit: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Kpi:
        await self._scope.require_company(actor, organization_id, company_id)
        if goal_id is not None:
            goal = await self._goals.get(company_id, goal_id)
            if goal is None:
                raise NotFoundError(f"goal {goal_id} not found")
        kpi = Kpi.create(
            company_id=company_id,
            name=name,
            direction=direction,
            target_value=target_value,
            goal_id=goal_id,
            description=description,
            unit=unit,
            metadata=metadata,
        )
        await self._kpis.add(kpi)
        await self._publish(
            "kpi.created",
            organization_id=organization_id,
            company_id=company_id,
            kpi_id=kpi.id,
            payload={
                "name": kpi.name,
                "direction": kpi.direction.value,
                "target": kpi.target_value,
            },
        )
        return kpi

    async def get_kpi(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId, kpi_id: KpiId
    ) -> Kpi:
        await self._scope.require_company(actor, organization_id, company_id)
        return await self._load(company_id, kpi_id)

    async def list_kpis(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        goal_id: GoalId | None = None,
    ) -> list[Kpi]:
        await self._scope.require_company(actor, organization_id, company_id)
        if goal_id is not None:
            return await self._kpis.list_for_goal(company_id, goal_id)
        return await self._kpis.list_for_company(company_id)

    async def update_kpi(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        kpi_id: KpiId,
        *,
        name: str | None = None,
        description: str | None = None,
        target_value: float | None = None,
        direction: KpiDirection | None = None,
        unit: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Kpi:
        await self._scope.require_company(actor, organization_id, company_id)
        kpi = await self._load(company_id, kpi_id)
        kpi = kpi.update(
            name=name,
            description=description,
            target_value=target_value,
            direction=direction,
            unit=unit,
            metadata=metadata,
        )
        await self._kpis.update(kpi)
        await self._publish(
            "kpi.updated",
            organization_id=organization_id,
            company_id=company_id,
            kpi_id=kpi.id,
            payload={"name": kpi.name, "target": kpi.target_value, "status": kpi.status.value},
        )
        return kpi

    async def record_measurement(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        kpi_id: KpiId,
        *,
        current_value: float,
    ) -> Kpi:
        """Record a measurement; re-derive status deterministically and emit
        ``kpi.measurement_recorded`` plus ``kpi.achieved`` / ``kpi.missed``
        when the KPI enters a terminal evaluation state."""
        await self._scope.require_company(actor, organization_id, company_id)
        kpi = await self._load(company_id, kpi_id)
        previous_status = kpi.status
        deadline_passed = await self._deadline_passed(company_id, kpi)
        kpi = kpi.record_measurement(current_value, deadline_passed=deadline_passed)
        await self._kpis.update(kpi)
        await self._publish(
            "kpi.measurement_recorded",
            organization_id=organization_id,
            company_id=company_id,
            kpi_id=kpi.id,
            payload={"current": kpi.current_value, "status": kpi.status.value},
        )
        if kpi.status is not previous_status:
            if kpi.status.value == "achieved":
                await self._publish(
                    "kpi.achieved",
                    organization_id=organization_id,
                    company_id=company_id,
                    kpi_id=kpi.id,
                    payload={"current": kpi.current_value, "target": kpi.target_value},
                )
            elif kpi.status.value == "missed":
                await self._publish(
                    "kpi.missed",
                    organization_id=organization_id,
                    company_id=company_id,
                    kpi_id=kpi.id,
                    payload={"current": kpi.current_value, "target": kpi.target_value},
                )
        return kpi

    async def _deadline_passed(self, company_id: CompanyId, kpi: Kpi) -> bool:
        """A KPI inherits deadline context from its owning goal's due date."""
        if kpi.goal_id is None:
            return False
        goal = await self._goals.get(company_id, kpi.goal_id)
        if goal is None or goal.due_at is None:
            return False
        return dt.datetime.now(dt.UTC) > goal.due_at

    async def _load(self, company_id: CompanyId, kpi_id: KpiId) -> Kpi:
        kpi = await self._kpis.get(company_id, kpi_id)
        if kpi is None:
            raise NotFoundError(f"kpi {kpi_id} not found")
        return kpi

    async def _publish(
        self,
        event_type: str,
        *,
        organization_id: OrganizationId,
        company_id: CompanyId,
        kpi_id: KpiId,
        payload: dict[str, Any],
    ) -> None:
        await emit(
            self._bus,
            self._store,
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload={"kpi_id": str(kpi_id), **payload},
            ),
        )
