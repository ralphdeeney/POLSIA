"""Deterministic verification of runtime results against generic criteria."""

from __future__ import annotations

import dataclasses
from typing import Any

from polsia.core.runtime.ports import RuntimeResult


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
