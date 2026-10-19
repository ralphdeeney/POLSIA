"""DeterministicBlueprintGenerator — offline, testable blueprint source.

No LLM, no network, no invented evidence: market/pricing/financial claims come
out labelled hypothesis (or unknown) because no external research tool backs
them. The output is a valid typed :class:`CompanyBlueprint` that passes
``CompanyBlueprint.validate()`` across every supported business-model family.
It is used for contract tests, offline E2E, CI and as the safe fallback when a
model-backed generator is not configured.
"""

from __future__ import annotations

import re
from typing import Any

from polsia.core.business.entities import (
    BusinessIdea,
    CompanyBlueprint,
    CompanyIdentity,
    Evidence,
    InitialGoal,
    InitialKpi,
    ProposedAgent,
)
from polsia.core.business.values import (
    BlueprintSource,
    BlueprintStatus,
    BusinessModel,
    EvidenceStatus,
)

#: Constraint keywords that produce explicit owner-action outputs (no paid
#: media, no external spend, zero budget).
_BUDGET_CONSTRAINT_HINTS = ("no paid ads", "no external spend", "zero budget")

_GOAL_TEMPLATES = {
    "demand_validation": (
        "Validate demand with 5 target customers",
        "5 recorded customer conversations with pain-point notes",
    ),
    "mvp_definition": (
        "Define the MVP scope for the next two weeks",
        "an MVP requirements note reconciled against explicit user constraints",
    ),
    "pricing_discovery": (
        "Test one pricing assumption with 3 target customers",
        "3 recorded pricing conversations; prices stay labelled hypotheses",
    ),
    "acquisition_channel": (
        "Identify two candidate acquisition channels",
        "a channel shortlist with costs deliberately marked unknown",
    ),
    "competitive_scan": (
        "Scan the competitive landscape qualitatively",
        "a competitor shortlist; every market claim stays unverified",
    ),
}

_MODEL_PRODUCT = {
    BusinessModel.SAAS: "a small hosted web application with email signup",
    BusinessModel.AI_AUTOMATION: "a scripted automation agent with human-in-the-loop review",
    BusinessModel.ECOMMERCE: "an online storefront with manual fulfilment first",
    BusinessModel.PHYSICAL_PRODUCT: "a small production batch with manual fulfilment",
    BusinessModel.MARKETPLACE: "a two-sided listing page that requires a supply seed strategy",
    BusinessModel.AGENCY: "a service package delivered manually with a written scope",
    BusinessModel.LOCAL_SERVICES: "a manual service offer with phone booking",
    BusinessModel.DIGITAL_EDUCATION: "a single self-paced course module",
    BusinessModel.SUBSCRIPTION: "a recurring membership with manual billing first",
    BusinessModel.OTHER: "a manually-scoped first offer with a written scope",
}

_MODEL_MVP = {
    BusinessModel.SAAS: "one core workflow behind an email/waiting list signup",
    BusinessModel.AI_AUTOMATION: "one automation workflow demonstrated on one live customer task",
    BusinessModel.ECOMMERCE: "one product SKU on a storefront with manual fulfilment",
    BusinessModel.PHYSICAL_PRODUCT: "one product prototype batch",
    BusinessModel.MARKETPLACE: "a manual match on one curated seller plus one listed category",
    BusinessModel.AGENCY: "one service package sold once manually",
    BusinessModel.LOCAL_SERVICES: "one bookable service slot",
    BusinessModel.DIGITAL_EDUCATION: "one lesson + outline shipped to a small pilot audience",
    BusinessModel.SUBSCRIPTION: "one recurring offer billed manually",
    BusinessModel.OTHER: "one manually-scoped offer delivered once",
}

_MODEL_REVENUE = {
    BusinessModel.SAAS: "recurring subscription billed monthly (hypothesis)",
    BusinessModel.AI_AUTOMATION: "per-automation-engagement fees with a recurring retainer hypothesis",
    BusinessModel.ECOMMERCE: "one-time sales on manual orders (hypothesis)",
    BusinessModel.PHYSICAL_PRODUCT: "unit sales with unverified unit economics (hypothesis)",
    BusinessModel.MARKETPLACE: "take rate on completed transactions (untested hypothesis)",
    BusinessModel.AGENCY: "per-engagement professional fees (hypothesis)",
    BusinessModel.LOCAL_SERVICES: "one-off local service fees (hypothesis)",
    BusinessModel.DIGITAL_EDUCATION: "per-course purchase (hypothesis)",
    BusinessModel.SUBSCRIPTION: "recurring membership fee (hypothesis)",
    BusinessModel.OTHER: "per-engagement fees (hypothesis)",
}

_MODEL_TECH = {
    BusinessModel.SAAS: ["hosted app runtime", "email signup form", "basic analytics"],
    BusinessModel.AI_AUTOMATION: ["automation runtime", "output review tooling", "basic analytics"],
    BusinessModel.ECOMMERCE: ["storefront", "manual order tracking", "email support inbox"],
    BusinessModel.PHYSICAL_PRODUCT: ["inventory ledger", "order tracking", "shipping workflow"],
    BusinessModel.MARKETPLACE: ["listing page", "manual match tracking", "email contact"],
    BusinessModel.AGENCY: ["project tracking", "written engagement scope", "email"],
    BusinessModel.LOCAL_SERVICES: ["manual scheduling ledger", "email contact"],
    BusinessModel.DIGITAL_EDUCATION: ["course hosting", "signup form", "feedback channel"],
    BusinessModel.SUBSCRIPTION: ["membership signup", "manual recurring billing"],
    BusinessModel.OTHER: ["manual tracking", "email contact"],
}

_MODEL_ROSTER = {
    BusinessModel.SAAS: ("growth agent", ["growth_operations"]),
    BusinessModel.AGENCY: ("client delivery agent", ["client_communication"]),
    BusinessModel.ECOMMERCE: ("storefront operations agent", ["storefront_operations"]),
    BusinessModel.SUBSCRIPTION: ("membership retention agent", ["retention"]),
    BusinessModel.DIGITAL_EDUCATION: ("course operations agent", ["course_operations"]),
    BusinessModel.MARKETPLACE: ("supply agent", ["supply_sourcing"]),
    BusinessModel.AI_AUTOMATION: ("automation delivery agent", ["automation_delivery"]),
    BusinessModel.LOCAL_SERVICES: ("dispatch agent", ["local_dispatch"]),
    BusinessModel.PHYSICAL_PRODUCT: ("inventory agent", ["inventory"]),
    BusinessModel.OTHER: ("general operations agent", ["operations"]),
}


def infer_business_model(text: str) -> BusinessModel:
    """Keyword-based business model inference (kept out of customer-facing copy).

    The result is mapped to :class:`BusinessModel` before the blueprint is
    validated; the raw keyword match is never presented as a market fact.
    """
    lowered = (text or "").lower()
    if "marketplace" in lowered:
        return BusinessModel.MARKETPLACE
    if "agency" in lowered or "consulting" in lowered or "marketing services" in lowered:
        return BusinessModel.AGENCY
    if "online course" in lowered or "education" in lowered or "training" in lowered:
        return BusinessModel.DIGITAL_EDUCATION
    if "subscription" in lowered or "membership" in lowered:
        return BusinessModel.SUBSCRIPTION
    if "e-commerce" in lowered or "ecommerce" in lowered or "physical goods" in lowered:
        return BusinessModel.ECOMMERCE
    if "physical product" in lowered or "hardware" in lowered or "apparel" in lowered:
        return BusinessModel.PHYSICAL_PRODUCT
    if "saas" in lowered or "software as a service" in lowered or "web app" in lowered:
        return BusinessModel.SAAS
    if any(
        k in lowered
        for k in (
            "ai reception",
            "ai agent",
            "ai work",
            "automation",
            "receptionist",
            "assistant",
        )
    ):
        return BusinessModel.AI_AUTOMATION
    if any(
        k in lowered
        for k in ("local services", "clinic", "restaurant", "salon", "repair", "cleaning")
    ):
        return BusinessModel.LOCAL_SERVICES
    return BusinessModel.OTHER


def _budget_constrained(idea: BusinessIdea) -> bool:
    constraints = [idea.budget_constraints or "", *idea.additional_constraints]
    lowered = [c.lower() for c in constraints if c]
    return any(hint in c for c in lowered for hint in _BUDGET_CONSTRAINT_HINTS)


def _contradictory_budget(idea: BusinessIdea) -> bool:
    """A zero-spend constraint plus a concrete spend instruction is a conflict.

    The blueprint must not silently pick one side: the conflict is surfaced as
    a risk, an owner action and a validation requirement so the owner decides.
    """
    if not _budget_constrained(idea):
        return False
    combined = " ".join([idea.budget_constraints or "", *idea.additional_constraints])
    return bool(re.search(r"\$\s*\d", combined))


def _company_name(idea: BusinessIdea, model: BusinessModel) -> str:
    base = (idea.description or "").strip()[:24]
    if not base:
        base = (idea.industry or model.value).title()[:24]
    slug = "".join(ch for ch in base if ch.isalnum() or ch.isspace()).strip().split()
    name = "-".join(slug) if slug else model.value
    return f"{name[:14]} ({model.value.replace('_', ' ')})"


class DeterministicBlueprintGenerator:
    """Blueprint source used by default offline; never fabricates evidence."""

    name = "deterministic-blueprint"

    async def generate(
        self, idea: BusinessIdea, *, context: dict[str, Any] | None = None
    ) -> CompanyBlueprint:
        _ = context  # reserved for prompt/context composition
        business_model = infer_business_model(f"{idea.industry or ''} {idea.description}")
        goals = self._goals()
        return CompanyBlueprint(
            idea_id=idea.id,
            organization_id=idea.organization_id,
            company_identity=CompanyIdentity(
                name=_company_name(idea, business_model),
                description=(idea.description or "").strip()[:400],
            ),
            business_model=business_model,
            source=BlueprintSource.DETERMINISTIC,
            business_idea_summary=self._summary(idea),
            customer_problem=self._customer_problem(idea),
            target_customer=self._target_customer(idea),
            value_proposition=self._value_proposition(idea),
            product_service=_MODEL_PRODUCT[business_model],
            revenue_model=_MODEL_REVENUE[business_model],
            target_market=idea.target_market,
            industry=idea.industry,
            differentiation=self._differentiation(idea),
            go_to_market=self._go_to_market(idea),
            customer_acquisition=self._customer_acquisition(idea),
            mvp=_MODEL_MVP[business_model],
            operational_requirements=self._operational_requirements(idea),
            technology_requirements=list(_MODEL_TECH[business_model]),
            pricing_hypotheses=[
                Evidence(
                    statement=self._pricing_statement(idea),
                    evidence_status=EvidenceStatus.HYPOTHESIS,
                )
            ],
            market_assumptions=[self._market_statement(idea)],
            competitive_landscape=[self._competitive_statement()],
            financial_assumptions=[self._financial_statement()],
            startup_risks=self._risks(idea, business_model),
            initial_goals=goals,
            initial_kpis=self._kpis(goals),
            proposed_agent_roster=self._roster(business_model),
            required_owner_actions=self._owner_actions(idea),
            validation_requirements=self._validation_requirements(idea),
            research_used=False,
        )

    # -- field builders (kept explicit so every line is auditable) -----------

    def _summary(self, idea: BusinessIdea) -> str:
        parts = [idea.description.strip()]
        if idea.target_market:
            parts.append(f"target market: {idea.target_market}")
        if idea.industry:
            parts.append(f"industry: {idea.industry}")
        if idea.geo_preferences:
            parts.append(f"geographic preferences: {', '.join(idea.geo_preferences)}")
        return "; ".join(parts)[:2800]

    def _customer_problem(self, idea: BusinessIdea) -> str:
        market = idea.target_market or "the stated target segment"
        return (
            f"{market} faces an undefined but symptom-implied problem. Problem must be confirmed "
            "by direct customer interviews; nothing here is evidence of severity."
        )

    def _target_customer(self, idea: BusinessIdea) -> str:
        if idea.target_market:
            return f"Primary target: {idea.target_market}."
        geo = ", ".join(idea.geo_preferences) if idea.geo_preferences else None
        suffix = f" (potentially in {geo})" if geo else ""
        return (
            "Primary target is unspecified; confirm a concrete first segment"
            f"{suffix} before defining the MVP."
        )

    def _value_proposition(self, idea: BusinessIdea) -> str:
        return (
            "The stated business idea suggests value for the chosen customer by "
            "removing a manual/friction step; the exact benefit framing is a "
            "hypothesis until customer interviews are recorded."
        )

    def _differentiation(self, idea: BusinessIdea) -> str:
        if idea.additional_constraints:
            return "Constraints are user-stated: " + "; ".join(idea.additional_constraints[:3])
        return "Differentiation is unverified; a competitive scan is required (unknown)."

    def _go_to_market(self, idea: BusinessIdea) -> str:
        if _budget_constrained(idea):
            return (
                "Channel plan relies on organic and direct-outreach loops only — "
                "no paid acquisition while the user's budget constraint stands."
            )
        return (
            "Channel plan is a hypothesis; begin with one direct-outreach loop and revisit "
            "after the first 10 customer conversations."
        )

    def _customer_acquisition(self, idea: BusinessIdea) -> str:
        return "Direct outreach plus one inbound content experiment; acquisition cost is unknown."

    def _operational_requirements(self, idea: BusinessIdea) -> list[str]:
        items = ["A written weekly operating checklist", "A customer-conversation log"]
        if _budget_constrained(idea):
            items.append("Zero-external-spend constraint honoured in every task plan")
        return items

    def _pricing_statement(self, idea: BusinessIdea) -> str:
        prefix = f'Pricing for "{idea.description[:60]}"'
        return f"{prefix} is assumed to fit a modest paid subscription; unverified hypothesis."

    def _market_statement(self, idea: BusinessIdea) -> Evidence:
        _ = idea
        return Evidence(
            statement="Market size is intentionally unknown; no external research tool was used.",
            evidence_status=EvidenceStatus.UNKNOWN,
        )

    def _competitive_statement(self) -> Evidence:
        return Evidence(
            statement="Competitor presence is intentionally unverified.",
            evidence_status=EvidenceStatus.UNKNOWN,
        )

    def _financial_statement(self) -> Evidence:
        return Evidence(
            statement="No revenue forecast is claimed; financials stay hypothesis.",
            evidence_status=EvidenceStatus.HYPOTHESIS,
        )

    def _risks(self, idea: BusinessIdea, model: BusinessModel) -> list[Evidence]:
        risks = [
            Evidence(
                statement="Demand has not been validated with any real customer yet.",
                evidence_status=EvidenceStatus.HYPOTHESIS,
            ),
            Evidence(
                statement="Business model fit is a keyword inference, not an evaluated decision.",
                evidence_status=EvidenceStatus.HYPOTHESIS,
            ),
        ]
        if _budget_constrained(idea):
            risks.append(
                Evidence(
                    statement="Budget constraint may limit channel coverage; "
                    "tradeoff must be surfaced to the owner.",
                    evidence_status=EvidenceStatus.HYPOTHESIS,
                )
            )
        if model is BusinessModel.MARKETPLACE:
            risks.append(
                Evidence(
                    statement="Marketplace liquidity on both sides is a known hard problem.",
                    evidence_status=EvidenceStatus.HYPOTHESIS,
                )
            )
        if _contradictory_budget(idea):
            risks.append(
                Evidence(
                    statement=(
                        "The stated constraints conflict: a zero/limited budget is "
                        "combined with a concrete spend plan. The tradeoff must be "
                        "resolved by the owner before any spend."
                    ),
                    evidence_status=EvidenceStatus.HYPOTHESIS,
                )
            )
        return risks

    def _owner_actions(self, idea: BusinessIdea) -> list[str]:
        actions = [
            "Confirm the first target customer segment in one sentence",
            "Provide any explicit pricing floor/ceiling before task planning",
        ]
        if idea.geo_preferences:
            actions.append(f"Confirm geographic scope: {', '.join(idea.geo_preferences)}")
        if _contradictory_budget(idea):
            actions.append(
                "Resolve the contradictory budget/spend constraints in writing before "
                "any purchase, ad spend or paid account activation."
            )
        return actions

    def _validation_requirements(self, idea: BusinessIdea) -> list[str]:
        base = [
            "Customer demand evidence (at least 5 recorded conversations)",
            "One pricing assumption tested with a real customer",
        ]
        if _budget_constrained(idea):
            base.append("Confirmation that the zero-external-spend constraint still holds")
        if _contradictory_budget(idea):
            base.append("Owner decision on which budget constraint prevails before any spend")
        return base

    def _goals(self) -> list[InitialGoal]:
        keys = ("demand_validation", "mvp_definition", "pricing_discovery", "acquisition_channel")
        return [
            InitialGoal(
                title=_GOAL_TEMPLATES[key][0],
                description=_GOAL_TEMPLATES[key][1],
                success_metric=_GOAL_TEMPLATES[key][1],
                priority="high" if index == 0 else "medium",
            )
            for index, key in enumerate(keys)
        ]

    def _kpis(self, goals: list[InitialGoal]) -> list[InitialKpi]:
        by_title = {goal.title: goal for goal in goals}
        return [
            InitialKpi(
                name="Customer conversations logged",
                goal_title=by_title["Validate demand with 5 target customers"].title,
                direction="increase",
                target_value=5,
                unit="conversations",
            ),
            InitialKpi(
                name="Pricing conversations logged",
                goal_title=by_title["Test one pricing assumption with 3 target customers"].title,
                direction="increase",
                target_value=3,
                unit="conversations",
            ),
            InitialKpi(
                name="Acquisition channels identified",
                goal_title=by_title["Identify two candidate acquisition channels"].title,
                direction="increase",
                target_value=2,
                unit="channels",
            ),
        ]

    def _roster(self, model: BusinessModel) -> list[ProposedAgent]:
        extra = _MODEL_ROSTER[model]
        return [
            ProposedAgent(
                name="company-ceo",
                role="ceo",
                capabilities=["company_strategy", "task_assignment"],
                description="Company-level coordinator; deterministic evidence-first behaviour.",
            ),
            ProposedAgent(
                name=f"{extra[0].replace(' ', '-')}",
                role=extra[0],
                capabilities=list(extra[1]),
                description=f"Role-specific {extra[0]}; capabilities used only for agent selection.",
            ),
        ]


def blueprint_status_of(_blueprint: CompanyBlueprint) -> BlueprintStatus:
    """A generated blueprint is a proposal until verification passes."""
    return BlueprintStatus.DRAFT
