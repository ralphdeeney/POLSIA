"""Value objects and enumerations for the business domain.

Status and evidence fields are explicit strings so every artifact can be
traced to its provenance: verified fact, sourced observation, inference,
hypothesis or unknown.
"""

from __future__ import annotations

import enum


class IntakeStatus(enum.StrEnum):
    """Lifecycle of a submitted business idea."""

    RECEIVED = "received"
    VALIDATED = "validated"
    REJECTED = "rejected"
    BLUEPRINT_GENERATED = "blueprint_generated"


class BlueprintStatus(enum.StrEnum):
    """Lifecycle of a company blueprint."""

    DRAFT = "draft"
    VALIDATED = "validated"
    REJECTED = "rejected"
    BOOTSTRAPPED = "bootstrapped"


class EvidenceStatus(enum.StrEnum):
    """Provenance of any claim in a blueprint or plan.

    Generated market sizes, competitor figures, customer counts, prices and
    financial forecasts must never be represented as verified facts without
    evidence. When no external research tool was used, mark claims as
    unverified (hypothesis / unknown).
    """

    VERIFIED_FACT = "verified_fact"
    SOURCED_OBSERVATION = "sourced_observation"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"
    UNKNOWN = "unknown"


class BusinessModel(enum.StrEnum):
    """Supported business model families. Not every idea is a SaaS company."""

    SAAS = "saas"
    ECOMMERCE = "ecommerce"
    AGENCY = "agency"
    AI_AUTOMATION = "ai_automation"
    LOCAL_SERVICES = "local_services"
    DIGITAL_EDUCATION = "digital_education"
    MARKETPLACE = "marketplace"
    SUBSCRIPTION = "subscription"
    PHYSICAL_PRODUCT = "physical_product"
    OTHER = "other"


class BlueprintSource(enum.StrEnum):
    """How a blueprint was produced."""

    DETERMINISTIC = "deterministic"
    MODEL = "model"
