"""Optional decision-advisory domain (Phase 4 mandate §8).

A minimal, optional port for *advisory* routing/classification decisions.
Its output is never authoritative: authorization, business rules and budgets
remain with the existing services. Anything the advisory says can be ignored;
it may never approve payments, grant permissions, create identity or execute
actions.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Any, Protocol

from polsia.core.shared.errors import ValidationError


class DecisionCategory(enum.StrEnum):
    """Business routing categories for agent/LLM work."""

    SIMPLE = "simple"
    RESEARCH = "research"
    ENGINEERING = "engineering"
    MARKETING = "marketing"
    SALES = "sales"
    SUPPORT = "support"
    FINANCE = "finance"
    MULTI_AGENT = "multi_agent"
    ADVERSARIAL = "adversarial"
    UNKNOWN = "unknown"


BUSINESS_CATEGORIES: tuple[DecisionCategory, ...] = (
    DecisionCategory.SIMPLE,
    DecisionCategory.RESEARCH,
    DecisionCategory.ENGINEERING,
    DecisionCategory.MARKETING,
    DecisionCategory.SALES,
    DecisionCategory.SUPPORT,
    DecisionCategory.FINANCE,
    DecisionCategory.MULTI_AGENT,
)


@dataclasses.dataclass(frozen=True)
class DecisionRequest:
    """One advisory classification request (tenant context is metadata only)."""

    text: str
    company_id: str
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.company_id).strip():
            raise ValidationError("company_id must not be empty")
        if not str(self.text).strip():
            raise ValidationError("text must not be empty")
        if len(self.text) > 10_000:
            raise ValidationError("text exceeds the advisory request bound")


@dataclasses.dataclass(frozen=True)
class DecisionAdvisory:
    """A non-authoritative advisory decision with traceability."""

    category: DecisionCategory
    confidence: float
    abstain: bool
    escalate: bool
    source: str  # "deterministic_baseline" | "laya" | "baseline_fallback" | ...
    model_version: str
    trace_id: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValidationError("confidence must be within [0, 1]")
        # An abstaining advisory must always recommend escalation.
        if self.abstain and not self.escalate:
            raise ValidationError("abstaining advisories must recommend escalation")
        if self.category is DecisionCategory.ADVERSARIAL and not self.escalate:
            raise ValidationError("adversarial detections must be escalated")


class DecisionAdvisoryPort(Protocol):
    """Advisory-only classifier port (optional integration point)."""

    name: str

    def classify(self, request: DecisionRequest) -> DecisionAdvisory: ...
