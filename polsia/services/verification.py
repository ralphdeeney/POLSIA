"""Deterministic verification of runtime results against generic criteria."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from polsia.core.business.errors import BlueprintValidationError
from polsia.core.business.values import EvidenceStatus
from polsia.core.runtime.ports import RuntimeResult

if TYPE_CHECKING:
    from polsia.core.business.entities import BusinessIdea, CompanyBlueprint


@dataclasses.dataclass(frozen=True)
class VerificationOutcome:
    passed: bool
    reason: str
    missing_fields: tuple[str, ...] = ()


class ResultVerifier:
    """Verify runtime status and declared structured output requirements.

    The verifier does not interpret natural-language claims from a runtime.
    It only checks structured, predeclared output-field requirements.
    """

    def verify(
        self, result: RuntimeResult, criteria: dict[str, Any] | None = None
    ) -> VerificationOutcome:
        if not result.is_success():
            return VerificationOutcome(False, result.error or "runtime reported failure")

        if criteria is not None and not isinstance(criteria, dict):
            return VerificationOutcome(False, "verification criteria are malformed")
        requirements = (criteria or {}).get("required_output_fields", [])
        if not isinstance(requirements, list) or any(
            not isinstance(field, str) or not field.strip() for field in requirements
        ):
            return VerificationOutcome(False, "verification criteria are malformed")

        missing = tuple(field for field in requirements if field not in result.output)
        if missing:
            return VerificationOutcome(
                False,
                "runtime output is missing required fields: " + ", ".join(missing),
                missing,
            )
        return VerificationOutcome(True, "runtime result satisfies declared criteria")


@dataclasses.dataclass(frozen=True)
class RecoveryDecision:
    retry: bool
    rationale: str
    retries_remaining: int


class RecoveryEngine:
    """Bounded retry policy; it recommends a retry and never executes it."""

    def __init__(self, *, max_retries: int = 2) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._max_retries = max_retries

    def decide(self, *, attempts: int, max_attempts: int) -> RecoveryDecision:
        """Use recorded attempts and a strict retry ceiling to bound recovery."""
        if attempts < 0 or max_attempts < 1:
            raise ValueError("attempts must be non-negative and max_attempts positive")
        if attempts == 0:
            return RecoveryDecision(False, "no failed attempt exists to retry", self._max_retries)
        retries_used = max(0, attempts - 1)
        remaining = min(self._max_retries - retries_used, max_attempts - attempts)
        if remaining <= 0:
            return RecoveryDecision(False, "retry budget exhausted; escalate", 0)
        return RecoveryDecision(True, "retry within configured attempt budget", remaining)


class ArtifactVerifier:
    """Task-type aware completion verification for generated artifacts.

    Completion rules depend on the task type (mandate §8):

    - *Artifact tasks* (e.g. blueprint generation) verify schema, required
      sections, internal consistency, correct ownership, provenance and
      persistence — they must NOT be forced through an external-tool evidence
      gate merely to finish.
    - *External-action verification* stays the Phase 4 rule: observable,
      trusted tool evidence (NativeAgentRuntime + ToolGateway) — unchanged.
    - *Research verification* requires traceable sources for factual claims
      and records uncertainty when evidence is unavailable (the blueprint's
      ``research_used`` flag + evidence statuses carry that contract).
    """

    def verify_blueprint_artifact(
        self,
        blueprint: CompanyBlueprint,
        *,
        persisted: BusinessIdea | None,
        criteria: dict[str, Any] | None = None,
    ) -> VerificationOutcome:
        """Verify a generated blueprint artifact end-to-end.

        ``persisted`` is the tenant-scoped idea row the artifact was written
        to; ``None`` means the artifact was never durably persisted and the
        outcome fails (persistence is part of the completion contract).
        """
        if criteria is not None and not isinstance(criteria, dict):
            return VerificationOutcome(False, "verification criteria are malformed")
        requirements = (criteria or {}).get("required_blueprint_sections", [])

        # 1. Schema completeness + internal consistency + evidence honesty.
        try:
            blueprint.validate()
        except BlueprintValidationError as exc:
            return VerificationOutcome(False, str(exc))

        # 2. Correct ownership: the artifact must belong to the same tenant and
        #    idea it claims to describe.
        if persisted is None:
            return VerificationOutcome(False, "artifact was not persisted; completion unverifiable")
        if persisted.organization_id != blueprint.organization_id:
            return VerificationOutcome(
                False, "artifact organization does not match the persisted idea's tenant"
            )
        if persisted.id != blueprint.idea_id:
            return VerificationOutcome(False, "artifact idea id does not match the persisted idea")

        # 3. Persistence: the stored payload must match the verified artifact.
        stored = persisted.blueprint
        if not isinstance(stored, dict) or stored != blueprint.to_dict():
            return VerificationOutcome(
                False, "persisted blueprint payload does not match the verified artifact"
            )

        # 4. Required section names (explicit criteria, when supplied).
        if requirements:
            missing_fields: list[str] = []
            for name in requirements:
                if not isinstance(name, str) or not name.strip():
                    return VerificationOutcome(False, "verification criteria are malformed")
                value = getattr(blueprint, name.strip(), None)
                is_empty = (
                    value is None
                    or (isinstance(value, str) and not value.strip())
                    or (isinstance(value, (list, tuple)) and not value)
                )
                if is_empty:
                    missing_fields.append(name.strip())
            if missing_fields:
                return VerificationOutcome(
                    False,
                    "blueprint is missing required sections: " + ", ".join(missing_fields),
                    tuple(missing_fields),
                )

        # 5. Provenance: research-claim honesty is enforced by validate()
        #    above; record the outcome reason so traceability stays explicit.
        provenance = (
            "no external research tool was used; all market/pricing/financial "
            "claims are labelled unverified"
            if not blueprint.research_used
            else "external research was declared by the generator"
        )
        return VerificationOutcome(True, f"artifact verification passed ({provenance})")


class ResearchVerifier:
    """Research-claim verification for source-backed factual assertions."""

    def verify_sources(self, claims: list[tuple[str, str, str | None]]) -> VerificationOutcome:
        """Every factual claim must carry a traceable source.

        Args:
            claims: ``(statement, evidence_status, source)`` triples —
                ``evidence_status`` is the raw :class:`EvidenceStatus` value.

        Claims labelled hypothesis/inference/unknown record uncertainty
        explicitly instead — uncertainty is a valid, recorded outcome, not a
        failure.
        """
        factual = frozenset(
            {EvidenceStatus.VERIFIED_FACT.value, EvidenceStatus.SOURCED_OBSERVATION.value}
        )
        problems: list[str] = []
        for statement, evidence_status, source in claims:
            if not isinstance(statement, str) or not statement.strip():
                problems.append("research claim has an empty statement")
                continue
            if evidence_status in factual and not (source and source.strip()):
                problems.append(f"untraceable factual claim: {statement.strip()}")
        if problems:
            return VerificationOutcome(False, "; ".join(problems))
        return VerificationOutcome(
            True, "all factual claims are traceable; uncertainty recorded where declared"
        )
