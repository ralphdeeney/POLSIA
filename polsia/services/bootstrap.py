"""CompanyBootstrapService — turn a validated blueprint into a company.

This is the boundary step that turns a *validated blueprint artifact* into a
fully-initialized company inside the existing control plane — it never
improvises company state:

- the company record/profile/strategy come from the blueprint;
- company goals + KPIs come from the blueprint's initial goals and KPIs
  (KPIs link to the seeded goals they reference by title);
- company-scoped, non-privileged agent entries (including the CEO
  configuration) come from the blueprint's ``proposed_agent_roster``;
- initial company memory gets one bootstrap-context record;
- planning readiness is recorded as explicit company strategy state.

Guarantees:

- every nested mutation goes through the *existing* domain services
  (CompanyService, GoalService, KpiService, AgentService, MemoryService);
  this service never manipulates persistence tables directly;
- idempotent by stable key: retrying the same idea re-uses the previously
  created company instead of duplicating companies/goals/agents;
- the company belongs to the authorized owner's organization; nothing is
  copied from any other company; no legal registration, domain purchase,
  spending or paid-account activation is triggered by this service;
- authorization is checked through nested services (each requires membership)
  and again here before any write.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from polsia.core.business.entities import BusinessIdea, CompanyBlueprint, blueprint_from_dict
from polsia.core.business.errors import BlueprintNotValidated
from polsia.core.business.ports import BusinessIdeaRepository
from polsia.core.business.values import BlueprintStatus
from polsia.core.planning.values import KpiDirection, Priority
from polsia.core.registry.entities import Agent
from polsia.core.shared.errors import NotFoundError, ValidationError
from polsia.core.shared.events import EventBus, EventEnvelope, EventStore, emit
from polsia.core.shared.ids import BusinessIdeaId, CompanyId, OrganizationId
from polsia.core.tenancy.entities import Company, User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.memory import MemoryService
from polsia.services.planning import GoalService, KpiService
from polsia.services.registries import AgentService
from polsia.services.tenancy import CompanyService


def bootstrap_idempotency_key(organization_id: OrganizationId, idea_id: BusinessIdeaId) -> str:
    """Stable, deterministic idempotency key for an (organization, idea) bootstrap."""
    return f"bootstrap:{organization_id}:{idea_id}"


def company_slug_for_idea(idea_id: BusinessIdeaId, company_name: str) -> str:
    """Deterministic slug derived from the blueprint name + idea identity.

    The idea-id suffix keeps repeated bootstraps of the same idea on the same
    slug while avoiding collisions across independent ideas.
    """
    idea_suffix = str(idea_id).rsplit("_", 1)[-1].lower()[-10:]
    base = "".join(ch if ch.isalnum() else "-" for ch in company_name.lower())
    collapsed = "-".join(part for part in base.split("-") if part)[:24].strip("-") or "company"
    slug = f"{collapsed}-{idea_suffix}"[:63].strip("-")
    if not slug:
        raise ValidationError("computed company slug is empty")
    return slug


@dataclasses.dataclass(frozen=True)
class BootstrapOutcome:
    """Everything the created company owns after one bootstrap call."""

    company: Company
    company_id: CompanyId
    goals: list[Any] = dataclasses.field(default_factory=list)
    kpis: list[Any] = dataclasses.field(default_factory=list)
    agents: list[Agent] = dataclasses.field(default_factory=list)
    memory_record_ids: list[str] = dataclasses.field(default_factory=list)
    idempotency_key: str = ""
    #: True when this call found an existing bootstrap (idempotent re-run).
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": str(self.company_id),
            "organization_id": str(self.company.organization_id),
            "slug": self.company.slug,
            "goal_ids": [str(getattr(goal, "id", "")) for goal in self.goals],
            "kpi_ids": [str(getattr(kpi, "id", "")) for kpi in self.kpis],
            "agent_ids": [str(agent.id) for agent in self.agents],
            "memory_record_ids": list(self.memory_record_ids),
            "idempotency_key": self.idempotency_key,
            "reused": self.reused,
        }


class CompanyBootstrapService:
    """Initialize a company from a validated blueprint."""

    def __init__(
        self,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        ideas: BusinessIdeaRepository,
        company_service: CompanyService,
        goal_service: GoalService,
        kpi_service: KpiService,
        agent_service: AgentService,
        memory_service: MemoryService,
        bus: EventBus,
        store: EventStore | None = None,
    ) -> None:
        self._companies = companies
        self._memberships = memberships
        self._ideas = ideas
        self._company_service = company_service
        self._goal_service = goal_service
        self._kpi_service = kpi_service
        self._agent_service = agent_service
        self._memory_service = memory_service
        self._bus = bus
        self._store = store

    # -- command -----------------------------------------------------------

    async def bootstrap_company(
        self,
        actor: User,
        organization_id: OrganizationId,
        idea_id: BusinessIdeaId,
    ) -> BootstrapOutcome:
        """Bootstrap (or idempotently re-return) the company for one idea.

        Steps:

        1. Load the tenant-scoped idea; require a *validated* blueprint.
        2. Detect an existing bootstrap and return it (idempotent re-run).
        3. Create the company via CompanyService (membership authorized).
        4. Seed strategy + planning-readiness state.
        5. Seed goals, KPIs (goal-linked), agents (incl. CEO) and memory.
        6. Emit ``company_bootstrap.completed``.
        """
        await self._require_member(actor, organization_id)
        idea = await self._get_idea(organization_id, idea_id)
        if idea.blueprint is None:
            raise BlueprintNotValidated(
                f"business idea {idea_id} has no generated blueprint to bootstrap"
            )
        if idea.blueprint_status is not BlueprintStatus.VALIDATED:
            raise BlueprintNotValidated(
                f"business idea {idea_id} blueprint is not validated; "
                "validation must precede bootstrap"
            )
        existing = await self._existing_bootstrap(organization_id, idea_id)
        if existing is not None:
            return dataclasses.replace(existing, reused=True)
        blueprint = _blueprint_from_idea(idea)
        key = bootstrap_idempotency_key(organization_id, idea_id)

        company = await self._create_company(actor, organization_id, idea_id, blueprint)
        company = await self._seed_strategy(
            actor, organization_id, company, idea_id, blueprint, key
        )
        await self._publish(
            "company_bootstrap.started",
            organization_id=organization_id,
            company_id=company.id,
            payload={"business_idea_id": str(idea_id), "idempotency_key": key},
        )
        goals, kpis, agents = await self._seed_entities(
            actor, organization_id, company.id, idea_id, blueprint
        )
        memory_ids = await self._seed_memory(
            actor, organization_id, company.id, idea_id, blueprint, company.name
        )
        await self._publish(
            "company_bootstrap.completed",
            organization_id=organization_id,
            company_id=company.id,
            payload={
                "business_idea_id": str(idea_id),
                "idempotency_key": key,
                "goal_count": len(goals),
                "kpi_count": len(kpis),
                "agent_count": len(agents),
            },
        )
        return BootstrapOutcome(
            company=company,
            company_id=company.id,
            goals=list(goals),
            kpis=list(kpis),
            agents=list(agents),
            memory_record_ids=memory_ids,
            idempotency_key=key,
            reused=False,
        )

    # -- internals ---------------------------------------------------------

    async def _create_company(
        self,
        actor: User,
        organization_id: OrganizationId,
        idea_id: BusinessIdeaId,
        blueprint: CompanyBlueprint,
    ) -> Company:
        identity = {
            "idea_id": str(idea_id),
            "business_model": blueprint.business_model.value,
            "target_customer": blueprint.target_customer,
            "value_proposition": blueprint.value_proposition,
        }
        return await self._company_service.create_company(
            actor,
            organization_id,
            name=blueprint.company_identity.name[:255] or "New Company",
            slug=company_slug_for_idea(idea_id, blueprint.company_identity.name),
            identity=identity,
            mission=self._mission(blueprint),
        )

    async def _seed_strategy(
        self,
        actor: User,
        organization_id: OrganizationId,
        company: Company,
        idea_id: BusinessIdeaId,
        blueprint: CompanyBlueprint,
        key: str,
    ) -> Company:
        strategy = {
            "bootstrap_idea_id": str(idea_id),
            "bootstrap_source": blueprint.source.value,
            "business_model": blueprint.business_model.value,
            "initial_strategy_summary": blueprint.business_idea_summary,
            "go_to_market": blueprint.go_to_market,
            "customer_acquisition": blueprint.customer_acquisition,
            "startup_risks": [risk.statement for risk in blueprint.startup_risks],
            "required_owner_actions": list(blueprint.required_owner_actions),
            "planning_readiness": {
                "validated": True,
                "ready_for_initial_plan": True,
                "goals_seeded": len(blueprint.initial_goals),
                "agents_seeded": len(blueprint.proposed_agent_roster),
            },
            "idempotency_key": key,
        }
        return await self._company_service.update_company(
            actor, organization_id, company.id, strategy=strategy
        )

    async def _seed_entities(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        idea_id: BusinessIdeaId,
        blueprint: CompanyBlueprint,
    ) -> tuple[list[Any], list[Any], list[Agent]]:
        """Create goals, KPIs and agents through the existing domain services."""
        goals = await self._seed_goals(actor, organization_id, company_id, idea_id, blueprint)
        goals_by_title = {goal.title: goal for goal in goals}
        kpis = await self._seed_kpis(
            actor, organization_id, company_id, idea_id, blueprint, goals_by_title
        )
        agents = await self._seed_agents(actor, organization_id, company_id, idea_id, blueprint)
        return goals, kpis, agents

    async def _seed_goals(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        idea_id: BusinessIdeaId,
        blueprint: CompanyBlueprint,
    ) -> list[Any]:
        goals = []
        for proposed in blueprint.initial_goals:
            try:
                priority = Priority(proposed.priority)
            except ValueError:
                priority = Priority.MEDIUM
            goal = await self._goal_service.create_goal(
                actor,
                organization_id,
                company_id,
                title=proposed.title[:255],
                description=proposed.description,
                priority=priority,
                metadata={
                    "success_metric": proposed.success_metric,
                    "blueprint_source": blueprint.source.value,
                    "bootstrap_idea_id": str(idea_id),
                },
            )
            goals.append(goal)
        return goals

    async def _seed_kpis(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        idea_id: BusinessIdeaId,
        blueprint: CompanyBlueprint,
        goals_by_title: dict[str, Any],
    ) -> list[Any]:
        kpis = []
        for proposed in blueprint.initial_kpis:
            try:
                direction = KpiDirection(proposed.direction)
            except ValueError:
                direction = KpiDirection.INCREASE
            goal = goals_by_title.get(proposed.goal_title)
            kpi = await self._kpi_service.create_kpi(
                actor,
                organization_id,
                company_id,
                name=proposed.name[:255],
                direction=direction,
                target_value=float(proposed.target_value),
                goal_id=goal.id if goal is not None else None,
                unit=proposed.unit,
                metadata={"bootstrap_idea_id": str(idea_id)},
            )
            kpis.append(kpi)
        return kpis

    async def _seed_agents(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        idea_id: BusinessIdeaId,
        blueprint: CompanyBlueprint,
    ) -> list[Agent]:
        agents = []
        for proposed in blueprint.proposed_agent_roster:
            agent = await self._agent_service.register_agent(
                actor,
                organization_id,
                company_id,
                name=proposed.name[:255],
                type=proposed.role[:100],
                description=proposed.description,
                capabilities=list(proposed.capabilities),
                configuration={
                    "bootstrap_idea_id": str(idea_id),
                    # Capabilities seed tool/skill eligibility only; grants
                    # happen exclusively through the permission engine.
                    "seed_capabilities": list(proposed.capabilities),
                },
            )
            agents.append(agent)
        return agents

    async def _seed_memory(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        idea_id: BusinessIdeaId,
        blueprint: CompanyBlueprint,
        company_name: str,
    ) -> list[str]:
        content = (
            f"Company '{company_name}' bootstrapped from business idea {idea_id}. "
            f"Mission: {self._mission(blueprint)} "
            f"Blueprint source: {blueprint.source.value}. All market, pricing and "
            "financial claims are unverified hypotheses until customer evidence "
            "is recorded."
        )
        record = await self._memory_service.write(
            actor,
            organization_id,
            company_id,
            scope="company",
            type="bootstrap_context",
            content=content[:8000],
            metadata={"bootstrap_idea_id": str(idea_id)},
            importance=0.9,
        )
        return [str(record.id)]

    async def _existing_bootstrap(
        self, organization_id: OrganizationId, idea_id: BusinessIdeaId
    ) -> BootstrapOutcome | None:
        """Idempotent re-run detection (also the compensation entry point)."""
        for company in await self._companies.list_for_organization(organization_id):
            marker = (company.strategy or {}).get("bootstrap_idea_id")
            if marker == str(idea_id):
                return BootstrapOutcome(
                    company=company,
                    company_id=company.id,
                    idempotency_key=bootstrap_idempotency_key(organization_id, idea_id),
                    reused=True,
                )
        return None

    async def _get_idea(
        self, organization_id: OrganizationId, idea_id: BusinessIdeaId
    ) -> BusinessIdea:
        idea = await self._ideas.get(organization_id, idea_id)
        if idea is None:
            raise NotFoundError(f"business idea {idea_id} not found")
        return idea

    @staticmethod
    def _mission(blueprint: CompanyBlueprint) -> str:
        summary = blueprint.business_idea_summary[:400]
        problem = blueprint.customer_problem[:400]
        return f"{summary} Customer problem: {problem}"

    async def _require_member(self, actor: User, organization_id: OrganizationId) -> None:
        """Defence-in-depth membership check before any nested service call."""
        from polsia.services.planning import _CompanyScope

        scope = _CompanyScope(self._companies, self._memberships)
        await scope.require_member(actor, organization_id)

    async def _publish(
        self,
        event_type: str,
        *,
        organization_id: OrganizationId,
        company_id: CompanyId,
        payload: dict[str, Any],
    ) -> None:
        await emit(
            self._bus,
            self._store,
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload=payload,
            ),
        )


def _blueprint_from_idea(idea: BusinessIdea) -> CompanyBlueprint:
    """Parse the persisted blueprint payload back into its typed artifact."""
    data = idea.blueprint
    if not isinstance(data, dict):
        raise BlueprintNotValidated("persisted blueprint payload is not a JSON object")
    blueprint = blueprint_from_dict(idea.id, idea.organization_id, data)
    blueprint.validate()
    return blueprint
