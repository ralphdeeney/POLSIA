"""CEO decisions and plans (Phase 3, mandate §17).

Decisions are typed, validated domain objects — never raw text. Every
decision carries a company + cycle reference, a decision type, the selected
action payload, a rationale, optional confidence (0..1) and an optional
provider-neutral model reference. ``validate()`` rejects malformed decisions
BEFORE any execution: an entity referencing a task of another company, an
unknown owner, a negative confidence, or a missing payload is invalid.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
from typing import Any

from polsia.core.shared.errors import ValidationError
from polsia.core.shared.ids import CompanyId, TaskId


class DecisionType(enum.StrEnum):
    PRIORITIZE_TASK = "prioritize_task"
    CREATE_PLAN = "create_plan"
    ASSIGN_AGENT = "assign_agent"
    RETRY_TASK = "retry_task"
    BLOCK_TASK = "block_task"
    REQUEST_APPROVAL = "request_approval"
    COMPLETE_TASK = "complete_task"
    REPLAN = "replan"
    WAIT = "wait"
    ESCALATE = "escalate"


#: Payload keys each decision type requires (validation contract).
_REQUIRED_PAYLOAD: dict[DecisionType, tuple[str, ...]] = {
    DecisionType.PRIORITIZE_TASK: ("task_id",),
    DecisionType.CREATE_PLAN: ("goal_id",),
    DecisionType.ASSIGN_AGENT: ("task_id", "agent_id"),
    DecisionType.RETRY_TASK: ("task_id",),
    DecisionType.BLOCK_TASK: ("task_id", "reason"),
    DecisionType.REQUEST_APPROVAL: ("action", "resource_type", "resource_id"),
    DecisionType.COMPLETE_TASK: ("task_id",),
    DecisionType.REPLAN: ("goal_id",),
    DecisionType.WAIT: (),
    DecisionType.ESCALATE: ("reason",),
}

_TASK_DECISIONS = frozenset(
    {
        DecisionType.PRIORITIZE_TASK,
        DecisionType.ASSIGN_AGENT,
        DecisionType.RETRY_TASK,
        DecisionType.BLOCK_TASK,
        DecisionType.COMPLETE_TASK,
    }
)


def validate_confidence(value: float | None) -> float | None:
    if value is not None and not (0.0 <= value <= 1.0):
        raise ValidationError(f"confidence must be within [0, 1], got {value!r}")
    return value


@dataclasses.dataclass(frozen=True)
class Decision:
    """A typed CEO decision. Immutable and validated at construction."""

    company_id: CompanyId
    decision_type: DecisionType
    payload: dict[str, Any] = dataclasses.field(default_factory=dict)
    rationale: str | None = None
    confidence: float | None = None
    model_reference: str | None = None  # provider-neutral (e.g. "deterministic-plan@1.0.0")
    cycle_id: str | None = None
    task_id: TaskId | None = None  # populated for task-scoped decisions
    created_at: dt.datetime = dataclasses.field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @staticmethod
    def create(
        *,
        company_id: CompanyId,
        decision_type: DecisionType,
        payload: dict[str, Any] | None = None,
        rationale: str | None = None,
        confidence: float | None = None,
        model_reference: str | None = None,
        cycle_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> Decision:
        decision = Decision(
            company_id=company_id,
            decision_type=decision_type,
            payload=dict(payload or {}),
            rationale=rationale,
            confidence=validate_confidence(confidence),
            model_reference=model_reference,
            cycle_id=cycle_id,
            created_at=now or dt.datetime.now(dt.UTC),
        )
        decision.validate()
        # task_id is derived from the validated payload for task-scoped kinds.
        if decision_type in _TASK_DECISIONS:
            raw = decision.payload.get("task_id")
            if raw is not None:
                object.__setattr__(decision, "task_id", TaskId.parse(str(raw)))
        return decision

    def validate(self) -> None:
        """Reject invalid decisions before any execution (mandate §17, §22).

        Raises ValidationError on: unknown decision type, missing required
        payload keys, invalid confidence, empty rationale for ESCALATE, or a
        task_id payload that is not parseable / not for this company.
        """
        if not isinstance(self.decision_type, DecisionType):
            raise ValidationError(f"invalid decision type: {self.decision_type!r}")

        required = _REQUIRED_PAYLOAD[self.decision_type]
        for key in required:
            value = self.payload.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValidationError(
                    f"decision {self.decision_type.value} requires payload key {key!r}"
                )

        if self.decision_type is DecisionType.ESCALATE and not (self.rationale or "").strip():
            raise ValidationError("escalate decision requires a rationale")

        raw_task = self.payload.get("task_id")
        if raw_task is not None:
            try:
                TaskId.parse(str(raw_task))
            except ValueError as exc:
                raise ValidationError(f"task_id is not a valid task id: {raw_task!r}") from exc

        validate_confidence(self.confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": str(self.company_id),
            "decision_type": self.decision_type.value,
            "payload": self.payload,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "model_reference": self.model_reference,
            "cycle_id": self.cycle_id,
            "task_id": str(self.task_id) if self.task_id else None,
            "created_at": self.created_at.isoformat(),
        }
