"""Business intake, blueprint and company bootstrap endpoints.

Thin HTTP surface over the wave-3 services. Everything is nested under the
owning organization (``/organizations/{org_id}/business-ideas/...``), and all
authorization + tenant scoping happens inside the services (ADR-0002) — a
router never touches a repository.

Workflow, in order:

1. ``POST   .../business-ideas``                    submit an idea
2. ``GET    .../business-ideas``                    list ideas
3. ``GET    .../business-ideas/{idea_id}``          fetch one idea
4. ``POST   .../business-ideas/{idea_id}/blueprint``   generate + validate
5. ``GET    .../business-ideas/{idea_id}/blueprint``   read the artifact
6. ``POST   .../business-ideas/{idea_id}/bootstrap``   create the company
7. ``POST   .../business-ideas/{idea_id}/ceo``         configure the CEO
8. ``POST   .../business-ideas/{idea_id}/initial-plan``CEO initial plan + tasks
9. ``POST   .../business-ideas/{idea_id}/reject``      withdraw an idea
"""

from __future__ import annotations

from fastapi import APIRouter, status

from polsia.api.deps import (
    BlueprintServiceDep,
    BusinessIdeaServiceDep,
    CeoInitializationServiceDep,
    CompanyBootstrapServiceDep,
    CurrentUser,
    parse_business_idea_id,
    parse_organization_id,
)
from polsia.api.schemas import (
    BlueprintOut,
    BootstrapOut,
    BusinessIdeaCreate,
    BusinessIdeaOut,
    BusinessIdeaReject,
    CeoInitOut,
    CompanyOut,
    InitialPlanOut,
)
from polsia.core.business.entities import BusinessIdea

router = APIRouter(prefix="/organizations/{org_id}/business-ideas", tags=["business"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=BusinessIdeaOut)
async def submit_business_idea(
    org_id: str,
    payload: BusinessIdeaCreate,
    user: CurrentUser,
    service: BusinessIdeaServiceDep,
) -> BusinessIdeaOut:
    """Submit a business idea (membership required; repeats are idempotent)."""
    idea = await service.submit_idea(
        user,
        parse_organization_id(org_id),
        raw_idea=payload.raw_idea,
        target_market=payload.target_market,
        industry=payload.industry,
        geo_preferences=payload.geo_preferences,
        budget_constraints=payload.budget_constraints,
        additional_constraints=payload.additional_constraints,
        idempotency_key=payload.idempotency_key,
    )
    return BusinessIdeaOut.model_validate(idea)


@router.get("", response_model=list[BusinessIdeaOut])
async def list_business_ideas(
    org_id: str,
    user: CurrentUser,
    service: BusinessIdeaServiceDep,
) -> list[BusinessIdeaOut]:
    """List the organization's business ideas (membership required)."""
    ideas = await service.list_ideas(user, parse_organization_id(org_id))
    return [BusinessIdeaOut.model_validate(idea) for idea in ideas]


@router.get("/{idea_id}", response_model=BusinessIdeaOut)
async def get_business_idea(
    org_id: str,
    idea_id: str,
    user: CurrentUser,
    service: BusinessIdeaServiceDep,
) -> BusinessIdeaOut:
    """Fetch one business idea (tenant-scoped; unknown ids are 404s)."""
    idea = await service.get_idea(
        user, parse_organization_id(org_id), parse_business_idea_id(idea_id)
    )
    return BusinessIdeaOut.model_validate(idea)


@router.post(
    "/{idea_id}/blueprint",
    status_code=status.HTTP_201_CREATED,
    response_model=BlueprintOut,
)
async def generate_blueprint(
    org_id: str,
    idea_id: str,
    user: CurrentUser,
    service: BlueprintServiceDep,
    ideas: BusinessIdeaServiceDep,
) -> BlueprintOut:
    """Generate + validate a blueprint for the idea (artifact verification)."""
    organization_id = parse_organization_id(org_id)
    parsed_idea_id = parse_business_idea_id(idea_id)
    idea = await service.generate_blueprint(
        user, organization_id, parsed_idea_id, ideas, context=None
    )
    return _blueprint_out(idea)


@router.get("/{idea_id}/blueprint", response_model=BlueprintOut)
async def get_blueprint(
    org_id: str,
    idea_id: str,
    user: CurrentUser,
    service: BlueprintServiceDep,
    ideas: BusinessIdeaServiceDep,
) -> BlueprintOut:
    """Read the generated blueprint artifact for the idea."""
    organization_id = parse_organization_id(org_id)
    parsed_idea_id = parse_business_idea_id(idea_id)
    await service.get_blueprint(user, organization_id, parsed_idea_id)
    idea = await ideas.get_idea(user, organization_id, parsed_idea_id)
    return _blueprint_out(idea)


@router.post(
    "/{idea_id}/bootstrap",
    status_code=status.HTTP_201_CREATED,
    response_model=BootstrapOut,
)
async def bootstrap_company(
    org_id: str,
    idea_id: str,
    user: CurrentUser,
    service: CompanyBootstrapServiceDep,
) -> BootstrapOut:
    """Create the company from the validated blueprint (idempotent by idea)."""
    outcome = await service.bootstrap_company(
        user, parse_organization_id(org_id), parse_business_idea_id(idea_id)
    )
    readiness = (outcome.company.strategy or {}).get("planning_readiness") or {}
    return BootstrapOut(
        company=CompanyOut.model_validate(outcome.company),
        company_id=str(outcome.company_id),
        goal_ids=[str(getattr(goal, "id", "")) for goal in outcome.goals],
        kpi_ids=[str(getattr(kpi, "id", "")) for kpi in outcome.kpis],
        agent_ids=[str(agent.id) for agent in outcome.agents],
        memory_record_ids=list(outcome.memory_record_ids),
        planning_ready=bool(readiness.get("ready_for_initial_plan")),
        idempotency_key=outcome.idempotency_key,
        reused=outcome.reused,
    )


@router.post(
    "/{idea_id}/ceo",
    status_code=status.HTTP_201_CREATED,
    response_model=CeoInitOut,
)
async def initialize_ceo(
    org_id: str,
    idea_id: str,
    user: CurrentUser,
    service: CeoInitializationServiceDep,
    bootstrap: CompanyBootstrapServiceDep,
) -> CeoInitOut:
    """Configure the bootstrapped CEO agent for the new company."""
    organization_id = parse_organization_id(org_id)
    parsed_idea_id = parse_business_idea_id(idea_id)
    outcome = await bootstrap.bootstrap_company(user, organization_id, parsed_idea_id)
    ceo = await service.initialize_ceo(user, organization_id, outcome.company_id, parsed_idea_id)
    return CeoInitOut(
        company_id=str(ceo.company.id),
        agent_id=str(ceo.agent.id),
        agent_name=ceo.agent.name,
        goal_count=ceo.goal_count,
        kpi_count=ceo.kpi_count,
        system_instructions=ceo.system_instructions,
    )


@router.post(
    "/{idea_id}/initial-plan",
    status_code=status.HTTP_201_CREATED,
    response_model=InitialPlanOut,
)
async def generate_initial_plan(
    org_id: str,
    idea_id: str,
    user: CurrentUser,
    service: CeoInitializationServiceDep,
    bootstrap: CompanyBootstrapServiceDep,
) -> InitialPlanOut:
    """Plan the first bootstrap goal and materialize it into real tasks."""
    organization_id = parse_organization_id(org_id)
    parsed_idea_id = parse_business_idea_id(idea_id)
    outcome = await bootstrap.bootstrap_company(user, organization_id, parsed_idea_id)
    plan = await service.generate_initial_plan(
        user, organization_id, outcome.company_id, parsed_idea_id
    )
    return InitialPlanOut(
        company_id=str(plan.company_id),
        goal_id=str(plan.goal_id),
        objective=plan.objective,
        plan=plan.plan,
        task_ids=list(plan.task_ids),
    )


@router.post("/{idea_id}/reject", response_model=BusinessIdeaOut)
async def reject_business_idea(
    org_id: str,
    idea_id: str,
    payload: BusinessIdeaReject,
    user: CurrentUser,
    service: BusinessIdeaServiceDep,
) -> BusinessIdeaOut:
    """Withdraw/reject an idea before a blueprint exists."""
    idea = await service.reject_idea(
        user,
        parse_organization_id(org_id),
        parse_business_idea_id(idea_id),
        reason=payload.reason,
    )
    return BusinessIdeaOut.model_validate(idea)


def _blueprint_out(idea: BusinessIdea) -> BlueprintOut:
    """Build the blueprint response from a persisted idea entity."""
    blueprint = idea.blueprint
    if not isinstance(blueprint, dict):
        from polsia.core.business.errors import BlueprintNotValidated

        raise BlueprintNotValidated("this idea has no generated blueprint yet")
    return BlueprintOut(
        idea_id=str(idea.id),
        organization_id=str(idea.organization_id),
        blueprint_status=idea.blueprint_status.value,
        blueprint_version=idea.blueprint_version,
        source=str(blueprint.get("source", "deterministic")),
        business_model=str(blueprint.get("business_model", "other")),
        research_used=bool(blueprint.get("research_used", False)),
        blueprint=blueprint,
    )
