"""Business domain entities: BusinessIdea and the typed CompanyBlueprint.

Pure frozen dataclasses + invariants, mirroring the rest of ``polsia.core``.
No ORM, no I/O. Validation raises :class:`BlueprintValidationError` /
:class:`ValidationError` from the shared/business taxonomy.

Evidence rules (client mandate §5): generated market sizes, competitor
figures, customer counts, prices and financial forecasts are never verified
facts without evidence. When no external research tool was used, any claim
labelled ``verified_fact`` / ``sourced_observation`` in market/pricing/
financial sections is a validation error — the blueprint must label them as
inference, hypothesis or unknown instead.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from polsia.core.business.errors import BlueprintValidationError
from polsia.core.business.values import (
    BlueprintSource,
    BlueprintStatus,
    BusinessModel,
    EvidenceStatus,
    IntakeStatus,
)
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import utcnow
from polsia.core.shared.ids import BusinessIdeaId, OrganizationId, UserId

MAX_IDEA_LENGTH = 8_000
MAX_SUMMARY_LENGTH = 4_000
MAX_SECTION_LENGTH = 4_000

#: Claim statuses that imply external evidence. Without a research pass they are invalid.
_EVIDENCE_REQUIRED_STATUSES = frozenset(
    {EvidenceStatus.VERIFIED_FACT, EvidenceStatus.SOURCED_OBSERVATION}
)


@dataclasses.dataclass(frozen=True)
class BusinessIdea:
    """A user-submitted business idea. ``raw_idea`` preserves the exact
    user-provided text; ``inferred_requirements`` holds anything derived from
    it, kept separate by design so provenance stays honest."""

    id: BusinessIdeaId
    organization_id: OrganizationId
    owner_user_id: UserId
    raw_idea: str
    description: str
    target_market: str | None = None
    industry: str | None = None
    geo_preferences: list[str] = dataclasses.field(default_factory=list)
    budget_constraints: str | None = None
    additional_constraints: list[str] = dataclasses.field(default_factory=list)
    inferred_requirements: dict[str, Any] = dataclasses.field(default_factory=dict)
    submitted_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    intake_status: IntakeStatus = IntakeStatus.RECEIVED
    blueprint_status: BlueprintStatus = BlueprintStatus.DRAFT
    blueprint: dict[str, Any] | None = None
    blueprint_version: int = 0
    #: Whether this in-memory object was actually persisted by the current
    #: service call (idempotent reuse paths set it to False).
    created_by_this_call: bool = True

    @staticmethod
    def submit(
        *,
        organization_id: OrganizationId,
        owner_user_id: UserId,
        raw_idea: str,
        description: str | None = None,
        target_market: str | None = None,
        industry: str | None = None,
        geo_preferences: list[str] | None = None,
        budget_constraints: str | None = None,
        additional_constraints: list[str] | None = None,
        now: dt.datetime | None = None,
    ) -> BusinessIdea:
        """Create an idea in RECEIVED intake status. Input validation fails fast."""
        if not raw_idea or not raw_idea.strip():
            raise ValidationError("business idea description must not be empty")
        if len(raw_idea) > MAX_IDEA_LENGTH:
            raise ValidationError(
                f"business idea exceeds maximum length ({len(raw_idea)} > {MAX_IDEA_LENGTH})"
            )
        text = description if description is not None else raw_idea
        if not text or not text.strip():
            raise ValidationError("business idea description must not be empty")
        return BusinessIdea(
            id=BusinessIdeaId.generate(),
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            raw_idea=raw_idea,
            description=text.strip(),
            target_market=target_market,
            industry=industry,
            geo_preferences=list(geo_preferences or []),
            budget_constraints=budget_constraints,
            additional_constraints=list(additional_constraints or []),
            submitted_at=now or utcnow(),
            intake_status=IntakeStatus.RECEIVED,
            blueprint_status=BlueprintStatus.DRAFT,
        )

    def _mutated(self, **changes: Any) -> BusinessIdea:
        return dataclasses.replace(self, **changes)

    def mark_validated(self) -> BusinessIdea:
        return self._mutated(intake_status=IntakeStatus.VALIDATED)

    def mark_rejected(self) -> BusinessIdea:
        return self._mutated(intake_status=IntakeStatus.REJECTED)

    def attach_blueprint(self, blueprint: dict[str, Any]) -> BusinessIdea:
        """Store the serialized blueprint verbatim. Status moves to GENERATED."""
        return self._mutated(
            blueprint=dict(blueprint),
            blueprint_version=self.blueprint_version + 1,
            intake_status=IntakeStatus.BLUEPRINT_GENERATED,
            blueprint_status=BlueprintStatus.DRAFT,
        )

    def mark_blueprint_validated(self) -> BusinessIdea:
        return self._mutated(blueprint_status=BlueprintStatus.VALIDATED)


@dataclasses.dataclass(frozen=True)
class Evidence:
    """A claim with explicit evidence provenance."""

    statement: str
    evidence_status: EvidenceStatus = EvidenceStatus.HYPOTHESIS
    source: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.statement, str) or not self.statement.strip():
            raise ValidationError("evidence statement must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "evidence_status": self.evidence_status.value,
            "source": self.source,
        }


@dataclasses.dataclass(frozen=True)
class ProposedAgent:
    """One agent role in the blueprint's proposed roster."""

    name: str
    role: str
    capabilities: list[str] = dataclasses.field(default_factory=list)
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "capabilities": list(self.capabilities),
            "description": self.description,
        }


@dataclasses.dataclass(frozen=True)
class InitialGoal:
    """A measurable business goal proposed for the new company."""

    title: str
    description: str | None = None
    success_metric: str | None = None
    priority: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "success_metric": self.success_metric,
            "priority": self.priority,
        }


@dataclasses.dataclass(frozen=True)
class InitialKpi:
    """A KPI proposed for the new company. Must reference an InitialGoal by title."""

    name: str
    goal_title: str
    #: Reuses the planning KpiDirection vocabulary (increase/decrease/maintain).
    direction: str = "increase"
    target_value: float = 0.0
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "goal_title": self.goal_title,
            "direction": self.direction,
            "target_value": self.target_value,
            "unit": self.unit,
        }


@dataclasses.dataclass(frozen=True)
class CompanyIdentity:
    """Name + optional description of the business entity to bootstrap."""

    name: str
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description}


def _evidence_lists(b: CompanyBlueprint) -> list[tuple[str, list[Evidence]]]:
    return [
        ("pricing_hypotheses", b.pricing_hypotheses),
        ("market_assumptions", b.market_assumptions),
        ("competitive_landscape", b.competitive_landscape),
        ("financial_assumptions", b.financial_assumptions),
        ("startup_risks", b.startup_risks),
    ]


@dataclasses.dataclass(frozen=True)
class CompanyBlueprint:
    """Typed artifact produced by a BlueprintPort (deterministic or model).

    ``source`` records provenance and ``research_used`` records whether any
    external research tool contributed evidence — both default to False for
    the offline/deterministic path, which forces claim statuses to fall in the
    unverified set.
    """

    idea_id: BusinessIdeaId
    organization_id: OrganizationId
    company_identity: CompanyIdentity
    business_model: BusinessModel
    source: BlueprintSource
    business_idea_summary: str
    customer_problem: str
    target_customer: str
    value_proposition: str
    product_service: str
    revenue_model: str
    target_market: str | None
    industry: str | None
    differentiation: str
    go_to_market: str
    customer_acquisition: str
    mvp: str
    operational_requirements: list[str]
    technology_requirements: list[str]
    pricing_hypotheses: list[Evidence]
    market_assumptions: list[Evidence]
    competitive_landscape: list[Evidence]
    financial_assumptions: list[Evidence]
    startup_risks: list[Evidence]
    initial_goals: list[InitialGoal]
    initial_kpis: list[InitialKpi]
    proposed_agent_roster: list[ProposedAgent]
    required_owner_actions: list[str]
    validation_requirements: list[str]
    generated_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    research_used: bool = False

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "idea_id": str(self.idea_id),
            "organization_id": str(self.organization_id),
            "company_identity": self.company_identity.to_dict(),
            "business_model": self.business_model.value,
            "source": self.source.value,
            "business_idea_summary": self.business_idea_summary,
            "customer_problem": self.customer_problem,
            "target_customer": self.target_customer,
            "value_proposition": self.value_proposition,
            "product_service": self.product_service,
            "revenue_model": self.revenue_model,
            "target_market": self.target_market,
            "industry": self.industry,
            "differentiation": self.differentiation,
            "go_to_market": self.go_to_market,
            "customer_acquisition": self.customer_acquisition,
            "mvp": self.mvp,
            "operational_requirements": list(self.operational_requirements),
            "technology_requirements": list(self.technology_requirements),
            "pricing_hypotheses": [e.to_dict() for e in self.pricing_hypotheses],
            "market_assumptions": [e.to_dict() for e in self.market_assumptions],
            "competitive_landscape": [e.to_dict() for e in self.competitive_landscape],
            "financial_assumptions": [e.to_dict() for e in self.financial_assumptions],
            "startup_risks": [e.to_dict() for e in self.startup_risks],
            "initial_goals": [g.to_dict() for g in self.initial_goals],
            "initial_kpis": [k.to_dict() for k in self.initial_kpis],
            "proposed_agent_roster": [a.to_dict() for a in self.proposed_agent_roster],
            "required_owner_actions": list(self.required_owner_actions),
            "validation_requirements": list(self.validation_requirements),
            "generated_at": self.generated_at.isoformat(),
            "research_used": self.research_used,
        }

    # -- validation ------------------------------------------------------------

    def validate(self) -> None:
        """Structural completeness + internal consistency + evidence honesty.

        Raises :class:`BlueprintValidationError` on the first invariant breach.
        """
        errors: list[str] = []
        self._check_scalars(errors)
        self._check_lists(errors)
        self._check_evidence_honesty(errors)
        self._check_kpi_goal_integrity(errors)
        self._check_roster(errors)
        self._check_measurable_goals(errors)
        if errors:
            raise BlueprintValidationError("; ".join(errors))

    def _check_scalars(self, errors: list[str]) -> None:
        if not isinstance(self.company_identity, CompanyIdentity) or (
            not self.company_identity.name.strip()
        ):
            errors.append("company_identity must declare a non-empty name")
        if not isinstance(self.business_model, BusinessModel):
            errors.append("business_model must be a BusinessModel")
        if (
            self.source is not BlueprintSource.DETERMINISTIC
            and self.source is not BlueprintSource.MODEL
        ):
            errors.append("source must be deterministic or model")
        scalar_fields: list[str] = [
            "business_idea_summary",
            "customer_problem",
            "target_customer",
            "value_proposition",
            "product_service",
            "revenue_model",
            "differentiation",
            "go_to_market",
            "customer_acquisition",
            "mvp",
        ]
        for name in scalar_fields:
            value = getattr(self, name)
            if not isinstance(value, str) or not str(value).strip():
                errors.append(f"{name} must be a non-empty string")
                continue
            if len(str(value)) > MAX_SECTION_LENGTH:
                errors.append(f"{name} exceeds maximum length {MAX_SECTION_LENGTH}")

    def _check_lists(self, errors: list[str]) -> None:
        for name in (
            "operational_requirements",
            "technology_requirements",
            "pricing_hypotheses",
            "market_assumptions",
            "competitive_landscape",
            "financial_assumptions",
            "startup_risks",
            "initial_goals",
            "initial_kpis",
            "proposed_agent_roster",
            "required_owner_actions",
            "validation_requirements",
        ):
            value = getattr(self, name)
            if not isinstance(value, list) or not value:
                errors.append(f"{name} must be a non-empty list")

    def _check_evidence_honesty(self, errors: list[str]) -> None:
        for section, claims in _evidence_lists(self):
            if not isinstance(claims, list):
                errors.append(f"{section} must be a list of claims")
                continue
            for claim in claims:
                if not isinstance(claim, Evidence):
                    errors.append(f"{section} entries must be Evidence")
                    continue
                if self.research_used:
                    # Sourced/verified claims stay allowed with declared provenance.
                    if claim.evidence_status in _EVIDENCE_REQUIRED_STATUSES and (
                        not claim.source or not claim.source.strip()
                    ):
                        errors.append(
                            f"{section} uses {claim.evidence_status.value} without a source: "
                            f"{claim.statement!r}"
                        )
                    continue
                if claim.evidence_status in _EVIDENCE_REQUIRED_STATUSES:
                    errors.append(
                        f"{section} labels {claim.statement!r} as "
                        f"{claim.evidence_status.value} but no external research tool "
                        "was used; unverified claims must be marked hypothesis/unknown"
                    )
                elif claim.evidence_status not in EvidenceStatus:
                    errors.append(
                        f"{section} has an unknown evidence_status for {claim.statement!r}"
                    )
                    continue
                elif len(claim.statement) > MAX_SECTION_LENGTH:
                    errors.append(f"{section} statement exceeds max length for {claim.statement!r}")

    def _check_kpi_goal_integrity(self, errors: list[str]) -> None:
        goal_titles = {goal.title.strip().lower() for goal in self.initial_goals}
        for kpi in self.initial_kpis:
            if not isinstance(kpi, InitialKpi):
                errors.append("initial_kpis entries must be InitialKpi")
                continue
            if kpi.goal_title.strip().lower() not in goal_titles:
                errors.append(
                    f"initial_kpi {kpi.name!r} references unknown goal {kpi.goal_title!r}"
                )

    def _check_roster(self, errors: list[str]) -> None:
        for agent in self.proposed_agent_roster:
            if not isinstance(agent, ProposedAgent):
                errors.append("proposed_agent_roster entries must be ProposedAgent")
                continue
            if not agent.name.strip() or not agent.role.strip():
                errors.append(f"proposed agent {agent.name!r} must declare name and role")
            if not agent.capabilities or any(not c.strip() for c in agent.capabilities):
                errors.append(f"proposed agent {agent.name!r} must declare capabilities")

    def _check_measurable_goals(self, errors: list[str]) -> None:
        for goal in self.initial_goals:
            if not isinstance(goal, InitialGoal):
                errors.append("initial_goals entries must be InitialGoal")
                continue
            if not goal.title.strip():
                errors.append("initial goal must have a title")
            if not goal.success_metric or not goal.success_metric.strip():
                errors.append(
                    f"initial goal {goal.title!r} must declare a measurable success metric"
                )


def _evidence_from_dict(raw: dict[str, Any]) -> Evidence:
    return Evidence(
        statement=str(raw["statement"]),
        evidence_status=EvidenceStatus(raw.get("evidence_status", "hypothesis")),
        source=raw.get("source"),
    )


def _generated_at_from_dict(data: dict[str, Any]) -> dt.datetime:
    """Preserve the artifact's persisted timestamp across reconstruction.

    Verification compares the reconstructed artifact with the exact stored
    payload, so dropping ``generated_at`` would make re-validation fail even
    for an untampered blueprint.
    """
    raw = data.get("generated_at")
    if isinstance(raw, str) and raw:
        try:
            parsed = dt.datetime.fromisoformat(raw)
        except ValueError:
            return utcnow()
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)
    return utcnow()


def blueprint_from_dict(
    idea_id: BusinessIdeaId,
    organization_id: OrganizationId,
    data: dict[str, Any],
) -> CompanyBlueprint:
    """Reconstruct a typed blueprint from its serialized form."""
    identity_raw = data.get("company_identity") or {}
    goals = [
        InitialGoal(
            title=str(raw["title"]),
            description=raw.get("description"),
            success_metric=raw.get("success_metric"),
            priority=str(raw.get("priority", "medium")),
        )
        for raw in (data.get("initial_goals") or [])
        if isinstance(raw, dict)
    ]
    kpis = [
        InitialKpi(
            name=str(raw["name"]),
            goal_title=str(raw["goal_title"]),
            direction=str(raw.get("direction", "increase")),
            target_value=float(raw.get("target_value", 0)),
            unit=raw.get("unit"),
        )
        for raw in (data.get("initial_kpis") or [])
        if isinstance(raw, dict)
    ]
    roster = [
        ProposedAgent(
            name=str(raw["name"]),
            role=str(raw["role"]),
            capabilities=[str(c) for c in raw.get("capabilities", [])],
            description=raw.get("description"),
        )
        for raw in (data.get("proposed_agent_roster") or [])
        if isinstance(raw, dict)
    ]
    evidence_lists: dict[str, list[Evidence]] = {}
    for name in (
        "pricing_hypotheses",
        "market_assumptions",
        "competitive_landscape",
        "financial_assumptions",
        "startup_risks",
    ):
        evidence_lists[name] = [
            _evidence_from_dict(raw) for raw in (data.get(name) or []) if isinstance(raw, dict)
        ]
    return CompanyBlueprint(
        idea_id=idea_id,
        organization_id=organization_id,
        company_identity=CompanyIdentity(
            name=str(identity_raw.get("name", "")),
            description=identity_raw.get("description"),
        ),
        business_model=BusinessModel(data.get("business_model", "other")),
        source=BlueprintSource(data.get("source", "deterministic")),
        business_idea_summary=str(data.get("business_idea_summary", "")),
        customer_problem=str(data.get("customer_problem", "")),
        target_customer=str(data.get("target_customer", "")),
        value_proposition=str(data.get("value_proposition", "")),
        product_service=str(data.get("product_service", "")),
        revenue_model=str(data.get("revenue_model", "")),
        target_market=data.get("target_market"),
        industry=data.get("industry"),
        differentiation=str(data.get("differentiation", "")),
        go_to_market=str(data.get("go_to_market", "")),
        customer_acquisition=str(data.get("customer_acquisition", "")),
        mvp=str(data.get("mvp", "")),
        operational_requirements=[str(x) for x in (data.get("operational_requirements") or [])],
        technology_requirements=[str(x) for x in (data.get("technology_requirements") or [])],
        pricing_hypotheses=evidence_lists["pricing_hypotheses"],
        market_assumptions=evidence_lists["market_assumptions"],
        competitive_landscape=evidence_lists["competitive_landscape"],
        financial_assumptions=evidence_lists["financial_assumptions"],
        startup_risks=evidence_lists["startup_risks"],
        initial_goals=goals,
        initial_kpis=kpis,
        proposed_agent_roster=roster,
        required_owner_actions=[str(x) for x in (data.get("required_owner_actions") or [])],
        validation_requirements=[str(x) for x in (data.get("validation_requirements") or [])],
        generated_at=_generated_at_from_dict(data),
        research_used=bool(data.get("research_used", False)),
    )
