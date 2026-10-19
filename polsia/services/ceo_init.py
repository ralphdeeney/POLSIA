"""CeoInitializationService — configure the bootstrapped CEO and its plan.

After :class:`~polsia.services.bootstrap.CompanyBootstrapService` has created
the company, this service:

1. configures the CEO agent with a blueprint-derived, bounded system
   instruction set (identity, mission, goals, KPIs, constraints, available
   agents, budget limits, approval policies);
2. generates the *initial executable plan* through the existing
   :class:`~polsia.services.planning_engine.PlanningEngine` — the deterministic
   planner stays the default; model-backed planning remains optional and
   behind the same port;
3. materializes the plan into real tasks (every task references its real goal
   inside the same company).

Authorization (ADR-0002) is enforced through the nested services and again
here before any write. No plan reaches persistence without passing
``Plan.validate()`` (PlanningEngine enforces this before materialization).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from polsia.core.business.entities import BusinessIdea, CompanyBlueprint
from polsia.core.business.errors import BlueprintNotValidated
from polsia.core.business.ports import BusinessIdeaRepository
from polsia.core.business.values import BlueprintStatus
from polsia.core.registry.entities import Agent
from polsia.core.shared.errors import NotFoundError
from polsia.core.shared.events import EventBus, EventEnvelope, EventStore, emit
from polsia.core.shared.ids import BusinessIdeaId, CompanyId, GoalId, OrganizationId
from polsia.core.tenancy.entities import Company, User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.planning import GoalService, _CompanyScope
from polsia.services.planning_engine import PlanningEngine
from polsia.services.registries import AgentService
from polsia.services.tenancy import CompanyService

_CEO_TYPES = ("ceo", "chief_executive")


@dataclasses.dataclass(frozen=True)
class CeoInitOutcome:
    """Result of one CEO initialization pass."""

    company: Company
    agent: Agent
    goal_count: int
    kpi_count: int
    system_instructions: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": str(self.company.id),
            "agent_id": str(self.agent.id),
            "agent_name": self.agent.name,
            "goal_count": self.goal_count,
            "kpi_count": self.kpi_count,
            "system_instructions": self.system_instructions,
        }


@dataclasses.dataclass(frozen=True)
class InitialPlanOutcome:
    """Result of the CEO's initial plan generation + materialization."""

    company_id: CompanyId
    goal_id: GoalId
    objective: str
    plan: dict[str, Any]
    task_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": str(self.company_id),
            "goal_id": str(self.goal_id),
            "objective": self.objective,
            "plan": self.plan,
            "task_ids": list(self.task_ids),
        }


class CeoInitializationService:
    """Configure the CEO agent and generate the initial plan."""

    def __init__(
        self,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        ideas: BusinessIdeaRepository,
        company_service: CompanyService,
        agent_service: AgentService,
        goal_service: GoalService,
        planning_engine: PlanningEngine,
        tasks_service: Any,
        bus: EventBus,
        store: EventStore | None = None,
    ) -> None:
        self._companies = companies
        self._memberships = memberships
        self._ideas = ideas
        self._company_service = company_service
        self._agent_service = agent_service
        self._goal_service = goal_service
        self._planning_engine = planning_engine
        self._tasks_service = tasks_service
        self._bus = bus
        self._store = store
        self._scope = _CompanyScope(companies, memberships)

    # -- commands -----------------------------------------------------------

    async def initialize_ceo(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        idea_id: BusinessIdeaId,
    ) -> CeoInitOutcome:
        """Configure the bootstrap-created CEO agent for its company."""
        await self._scope.require_member(actor, organization_id)
        idea = await self._get_idea(organization_id, idea_id)
        blueprint = _validated_blueprint(idea)
        company = await self._company_service.get_company(actor, organization_id, company_id)
        _require_bootstrap_for_idea(company, idea_id)

        ceo = await self._find_ceo(actor, organization_id, company_id)
        goals = await self._goal_service.list_goals(actor, organization_id, company_id)
        seeded_goals = [
            goal for goal in goals if (goal.metadata or {}).get("bootstrap_idea_id") == str(idea_id)
        ]
        instructions = self._instructions(company, blueprint, seeded_goals)
        configured = await self._agent_service.update_agent(
            actor,
            organization_id,
            company_id,
            ceo.id,
            system_instructions=instructions,
            configuration={
                **(ceo.configuration or {}),
                "ceo_initialized": True,
                "initialized_for_idea": str(idea_id),
                "constraints": self._constraints(blueprint),
                "approval_policy": {
                    "external_actions": "require_approval",
                    "spending": "forbidden",
                    "artifact_actions": "auto_verify",
                },
            },
        )
        await self._publish(
            "ceo.initialized",
            organization_id=organization_id,
            company_id=company_id,
            payload={
                "business_idea_id": str(idea_id),
                "agent_id": str(configured.id),
                "goal_count": len(seeded_goals),
            },
        )
        return CeoInitOutcome(
            company=company,
            agent=configured,
            goal_count=len(seeded_goals),
            kpi_count=len(blueprint.initial_kpis),
            system_instructions=instructions,
        )

    async def generate_initial_plan(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        idea_id: BusinessIdeaId,
    ) -> InitialPlanOutcome:
        """Plan the first seeded goal and materialize it into real tasks.

        The deterministic DecompositionPort produces the drafts; the existing
        PlanningEngine validates and materializes them — tasks always
        reference the real goal within this company.
        """
        await self._scope.require_company(actor, organization_id, company_id)
        idea = await self._get_idea(organization_id, idea_id)
        blueprint = _validated_blueprint(idea)
        company = await self._company_service.get_company(actor, organization_id, company_id)
        _require_bootstrap_for_idea(company, idea_id)

        goals = await self._goal_service.list_goals(actor, organization_id, company_id)
        seeded = [
            goal for goal in goals if (goal.metadata or {}).get("bootstrap_idea_id") == str(idea_id)
        ]
        if not seeded:
            raise NotFoundError(f"company {company_id} has no bootstrap goals for idea {idea_id}")
        goal = seeded[0]
        plan = await self._planning_engine.plan_goal(
            actor,
            organization_id,
            company_id,
            goal.id,
            objective=goal.title,
            context=self._plan_context(company, blueprint, goal),
        )
        tasks = await self._planning_engine.materialize(
            actor,
            organization_id,
            company_id,
            plan,
            tasks_service=self._tasks_service,
        )
        await self._publish(
            "ceo.initial_plan_created",
            organization_id=organization_id,
            company_id=company_id,
            payload={
                "business_idea_id": str(idea_id),
                "goal_id": str(goal.id),
                "task_count": len(tasks),
                "planner": plan.constraints.get("generated_by", "unknown"),
            },
        )
        return InitialPlanOutcome(
            company_id=company_id,
            goal_id=goal.id,
            objective=plan.objective,
            plan=plan.to_dict(),
            task_ids=[str(task.id) for task in tasks],
        )

    # -- internals ----------------------------------------------------------

    def _instructions(
        self,
        company: Company,
        blueprint: CompanyBlueprint,
        seeded_goals: list[Any],
    ) -> str:
        """Bounded CEO instruction set assembled from trusted internal state."""
        goal_lines = "\n".join(
            f"- {goal.title} (metric: {(goal.metadata or {}).get('success_metric', 'n/a')})"
            for goal in seeded_goals[:10]
        )
        kpi_lines = "\n".join(
            f"- {kpi.name}: target {kpi.target_value} ({kpi.direction})"
            for kpi in blueprint.initial_kpis[:10]
        )
        agent_lines = "\n".join(
            f"- {agent.name} ({agent.role})" for agent in blueprint.proposed_agent_roster[:10]
        )
        constraint_lines = "\n".join(f"- {item}" for item in self._constraints(blueprint))
        return (
            f"You coordinate company '{company.name}'.\n"
            f"Mission: {company.mission}\n\n"
            "Evidence policy: never present market size, competitor counts, prices "
            "or financial figures as facts. Label unverified claims "
            "hypothesis/unknown and verify through customer evidence.\n\n"
            "Budget/approval policy: no spending, no external account activation, "
            "no legal registration. External actions require owner approval; "
            "artifact work completes through verified output.\n\n"
            f"Constraints:\n{constraint_lines}\n\n"
            f"Initial goals:\n{goal_lines or '- (none seeded)'}\n\n"
            f"Initial KPIs:\n{kpi_lines or '- (none)'}\n\n"
            f"Available agent roles:\n{agent_lines or '- (none)'}\n"
        )[:8000]

    @staticmethod
    def _constraints(blueprint: CompanyBlueprint) -> list[str]:
        constraints = [
            "no company-level spending",
            "no external account activation without owner approval",
        ]
        if blueprint.target_market:
            constraints.append(f"declared target market: {blueprint.target_market}")
        if blueprint.required_owner_actions:
            constraints.extend(
                f"owner action pending: {item}" for item in blueprint.required_owner_actions[:5]
            )
        return constraints

    def _plan_context(
        self,
        company: Company,
        blueprint: CompanyBlueprint,
        goal: Any,
    ) -> dict[str, Any]:
        metric = (goal.metadata or {}).get("success_metric") or goal.title
        return {
            "objectives": [
                f"Produce first verifiable evidence for '{goal.title}' — success when: {metric}",
            ],
            "company_identity": {
                "company_id": str(company.id),
                "business_model": blueprint.business_model.value,
                "value_proposition": blueprint.value_proposition,
                "customer_acquisition": blueprint.customer_acquisition,
            },
            "approval_policy": "external actions require owner approval; spending forbidden",
        }

    async def _find_ceo(
        self, actor: User, organization_id: OrganizationId, company_id: CompanyId
    ) -> Agent:
        """Locate the CEO agent among this company's agents (tenant-scoped)."""
        agents = await self._agent_service.list_agents(actor, organization_id, company_id)
        for agent_type in _CEO_TYPES:
            for agent in agents:
                if agent.type == agent_type:
                    return agent
        for agent in agents:
            if "ceo" in agent.name.lower():
                return agent
        raise NotFoundError(f"company {company_id} has no CEO agent to initialize")

    async def _get_idea(
        self, organization_id: OrganizationId, idea_id: BusinessIdeaId
    ) -> BusinessIdea:
        idea = await self._ideas.get(organization_id, idea_id)
        if idea is None:
            raise NotFoundError(f"business idea {idea_id} not found")
        return idea

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


def _validated_blueprint(idea: BusinessIdea) -> CompanyBlueprint:
    if idea.blueprint is None:
        raise BlueprintNotValidated(f"business idea {idea.id} has no blueprint")
    if idea.blueprint_status is not BlueprintStatus.VALIDATED:
        raise BlueprintNotValidated(f"business idea {idea.id} blueprint is not validated")
    from polsia.services.bootstrap import _blueprint_from_idea

    return _blueprint_from_idea(idea)


def _require_bootstrap_for_idea(company: Company, idea_id: BusinessIdeaId) -> None:
    strategy = company.strategy or {}
    marker = strategy.get("bootstrap_idea_id")
    if marker != str(idea_id):
        raise NotFoundError(f"company {company.id} was not bootstrapped from idea {idea_id}")
    readiness = strategy.get("planning_readiness") or {}
    if not readiness.get("ready_for_initial_plan"):
        raise BlueprintNotValidated(
            f"company {company.id} is not marked ready for initial planning"
        )
