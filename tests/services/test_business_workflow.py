"""Phase 4 wave 3 — business intake -> blueprint -> bootstrap -> CEO plan.

Offline integration tests on SQLite: the complete happy path plus the
lifecycle and authorization rails (idempotency, tenant isolation,
blueprint-not-validated rejection).
"""

from __future__ import annotations

from typing import Any

import pytest
from polsia.adapters.business.deterministic import DeterministicBlueprintGenerator
from polsia.adapters.events.inprocess import InProcessEventBus
from polsia.adapters.persistence.business_repositories import SqlAlchemyBusinessIdeaRepository
from polsia.adapters.persistence.event_store import SqlAlchemyEventStore
from polsia.adapters.persistence.memory_repositories import SqlAlchemyMemoryStore
from polsia.adapters.persistence.registry_repositories import SqlAlchemyAgentRepository
from polsia.adapters.persistence.repositories import (
    SqlAlchemyCompanyRepository,
    SqlAlchemyGoalRepository,
    SqlAlchemyKpiRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from polsia.adapters.persistence.task_repositories import (
    SqlAlchemyTaskDependencyRepository,
    SqlAlchemyTaskRepository,
)
from polsia.adapters.security.passwords import BcryptPasswordHasher
from polsia.core.auth.service import AuthService
from polsia.core.business.errors import BlueprintNotValidated
from polsia.core.business.values import IntakeStatus
from polsia.core.tenancy.entities import Organization
from polsia.core.tenancy.values import MembershipRole
from polsia.infra.db import create_all_metadata
from polsia.services.blueprint_service import BlueprintService
from polsia.services.bootstrap import CompanyBootstrapService
from polsia.services.business_intake import BusinessIdeaService
from polsia.services.ceo_init import CeoInitializationService
from polsia.services.memory import MemoryService
from polsia.services.planning import GoalService, KpiService
from polsia.services.planning_engine import PlanningEngine
from polsia.services.registries import AgentService
from polsia.services.tasks import TaskService
from polsia.services.tenancy import CompanyService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


class _NullTokens:
    def issue_access_token(self, user_id: object) -> str:
        return ""

    def issue_refresh_token(self, user_id: object) -> str:
        return ""

    def verify_token(self, token: str) -> object:
        raise ValueError("no tokens in this test env")


async def _make_env() -> dict[str, Any]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    await create_all_metadata(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    session = factory()
    bus = InProcessEventBus()
    store = SqlAlchemyEventStore(session)

    users = SqlAlchemyUserRepository(session)
    auth = AuthService(users, BcryptPasswordHasher(), _NullTokens())
    owner = await auth.register("owner@example.com", "password-123", "Owner")
    other = await auth.register("other@example.com", "password-123", "Other")

    orgs = SqlAlchemyOrganizationRepository(session)
    memberships = SqlAlchemyMembershipRepository(session)
    from polsia.core.tenancy.entities import OrgMembership

    org_a = Organization.create(name="Owner Org", slug="owner-org", owner_user_id=owner.id)
    org_b = Organization.create(name="Other Org", slug="other-org", owner_user_id=other.id)
    await orgs.add(org_a)
    await orgs.add(org_b)
    await memberships.add(
        OrgMembership.create(organization_id=org_a.id, user_id=owner.id, role=MembershipRole.OWNER)
    )
    await memberships.add(
        OrgMembership.create(organization_id=org_b.id, user_id=other.id, role=MembershipRole.OWNER)
    )

    ideas = SqlAlchemyBusinessIdeaRepository(session)
    companies_repo = SqlAlchemyCompanyRepository(session)
    goals_repo = SqlAlchemyGoalRepository(session)
    kpis_repo = SqlAlchemyKpiRepository(session)
    agents_repo = SqlAlchemyAgentRepository(session)
    memory = SqlAlchemyMemoryStore(session)
    tasks_repo = SqlAlchemyTaskRepository(session)
    deps_repo = SqlAlchemyTaskDependencyRepository(session)

    company_service = CompanyService(companies_repo, memberships, bus, store=store)
    goal_service = GoalService(goals_repo, companies_repo, memberships, bus, store=store)
    kpi_service = KpiService(kpis_repo, goals_repo, companies_repo, memberships, bus, store=store)
    agent_service = AgentService(agents_repo, companies_repo, memberships, bus, store=store)
    memory_service = MemoryService(memory, companies_repo, memberships, bus, store=store)
    task_service = TaskService(tasks_repo, deps_repo, companies_repo, memberships, bus, store=store)
    idea_service = BusinessIdeaService(ideas, companies_repo, memberships, bus, store=store)
    generator = DeterministicBlueprintGenerator()
    blueprint_service = BlueprintService(
        ideas, generator, companies_repo, memberships, bus, store=store
    )
    bootstrap_service = CompanyBootstrapService(
        companies_repo,
        memberships,
        ideas,
        company_service,
        goal_service,
        kpi_service,
        agent_service,
        memory_service,
        bus,
        store=store,
    )
    planning_engine = PlanningEngine(
        _DeterministicDecomposer(),
        goals=goals_repo,
        companies=companies_repo,
        memberships=memberships,
    )
    ceo_service = CeoInitializationService(
        companies_repo,
        memberships,
        ideas,
        company_service,
        agent_service,
        goal_service,
        planning_engine,
        task_service,
        bus,
        store=store,
    )
    return {
        "engine": engine,
        "session": session,
        "owner": owner,
        "other": other,
        "owner_org": org_a.id,
        "other_org": org_b.id,
        "idea_service": idea_service,
        "blueprint_service": blueprint_service,
        "bootstrap_service": bootstrap_service,
        "ceo_service": ceo_service,
        "company_service": company_service,
        "goal_service": goal_service,
    }


class _DeterministicDecomposer:
    """Minimal OrchestratorPort compatible with PlanningEngine."""

    name = "deterministic-test"

    async def decompose(self, **_: Any) -> Any:
        from polsia.core.scheduling import DecompositionPlan, TaskDraft

        return DecompositionPlan(
            tasks=[
                TaskDraft(
                    title="Draft initial evidence task",
                    metadata={"generated_by": self.name},
                )
            ],
            rationale="single deterministic task",
            metadata={},
        )


@pytest.mark.asyncio
async def test_full_pipeline_from_idea_to_initial_plan() -> None:
    env = await _make_env()
    owner, org = env["owner"], env["owner_org"]
    idea = await env["idea_service"].submit_idea(
        owner,
        org,
        raw_idea="An online scheduling SaaS for independent tutors",
        target_market="independent tutors",
        industry="SaaS",
        additional_constraints=["no paid ads until first revenue"],
        idempotency_key="key-1",
    )
    assert idea.intake_status is IntakeStatus.VALIDATED
    assert idea.description == "An online scheduling SaaS for independent tutors"

    # Idempotent repeat: same key + payload returns the same idea.
    repeated = await env["idea_service"].submit_idea(
        owner,
        org,
        raw_idea="An online scheduling SaaS for independent tutors",
        target_market="independent tutors",
        industry="SaaS",
        additional_constraints=["no paid ads until first revenue"],
        idempotency_key="key-1",
    )
    assert repeated.id == idea.id
    assert repeated.created_by_this_call is False

    idea = await env["blueprint_service"].generate_blueprint(
        owner, org, idea.id, env["idea_service"], context={}
    )
    assert idea.blueprint is not None
    assert idea.blueprint_status.value == "validated"

    outcome = await env["bootstrap_service"].bootstrap_company(owner, org, idea.id)
    company = outcome.company
    assert outcome.reused is False
    assert company.slug.startswith("online-scheduling-saas-") or company.slug
    assert len(outcome.goals) == 4
    assert len(outcome.kpis) == 3
    assert len(outcome.agents) == 2

    # Idempotent bootstrap: retry returns the same company, no duplicates.
    retry = await env["bootstrap_service"].bootstrap_company(owner, org, idea.id)
    assert retry.company.id == outcome.company.id

    ceo_outcome = await env["ceo_service"].initialize_ceo(owner, org, outcome.company_id, idea.id)
    assert ceo_outcome.agent.type == "ceo"
    assert "Evidence policy" in ceo_outcome.system_instructions

    plan_outcome = await env["ceo_service"].generate_initial_plan(
        owner, org, outcome.company_id, idea.id
    )
    assert plan_outcome.goal_id
    assert plan_outcome.plan["objective"]
    assert len(plan_outcome.task_ids) == 1

    await _close(env)


@pytest.mark.asyncio
async def test_bootstrap_requires_validated_blueprint() -> None:
    env = await _make_env()
    owner, org = env["owner"], env["owner_org"]
    idea = await env["idea_service"].submit_idea(
        owner, org, raw_idea="A cookie bakery for corporate events"
    )
    with pytest.raises(BlueprintNotValidated):
        await env["bootstrap_service"].bootstrap_company(owner, org, idea.id)
    await _close(env)


@pytest.mark.asyncio
async def test_cross_tenant_idea_access_is_denied() -> None:
    env = await _make_env()
    owner, other = env["owner"], env["other"]
    org = env["owner_org"]
    other_org = env["other_org"]
    try:
        idea = await env["idea_service"].submit_idea(
            owner, org, raw_idea="A meal-prep delivery subscription for nurses"
        )
        from polsia.core.business.errors import BusinessIdeaNotFound
        from polsia.core.shared.errors import NotAMember

        # (1) A user who is not a member of the owning organization is denied
        #     before any data is touched.
        with pytest.raises(NotAMember):
            await env["idea_service"].get_idea(other, org, idea.id)

        # (2) A member of a *different* organization, scoped to their own
        #     organization, cannot see another tenant's idea id at all.
        with pytest.raises(BusinessIdeaNotFound):
            await env["idea_service"].get_idea(other, other_org, idea.id)
    finally:
        await _close(env)


async def _close(env: dict[str, Any]) -> None:
    await env["session"].rollback()
    await env["session"].close()
    await env["engine"].dispose()
