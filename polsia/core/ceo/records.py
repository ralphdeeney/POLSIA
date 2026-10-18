"""Durable records of validated CEO decisions."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from typing import Any, Protocol

from polsia.core.ceo.decisions import Decision, DecisionType
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import utcnow
from polsia.core.shared.ids import CompanyId, DecisionRecordId, OrganizationId, TaskId

MAX_DECISION_RECORD_JSON_BYTES = 8192


def validate_decision_record_json(payload: dict[str, Any], outcome_context: dict[str, Any]) -> None:
    try:
        serialized = json.dumps(
            {"payload": payload, "outcome_context": outcome_context},
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = serialized.encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
        raise ValidationError("decision record data must be valid JSON") from exc
    if len(encoded) > MAX_DECISION_RECORD_JSON_BYTES:
        raise ValidationError(
            f"combined decision payload and outcome context exceed "
            f"{MAX_DECISION_RECORD_JSON_BYTES} UTF-8 bytes"
        )


def _copy_json(value: Any) -> Any:
    """Copy JSON-compatible data through a serialization boundary."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValidationError("decision record payload must be JSON serializable") from exc


@dataclasses.dataclass(frozen=True)
class DecisionRecord:
    id: DecisionRecordId
    organization_id: OrganizationId
    company_id: CompanyId
    decision: Decision
    observation_digest: str
    outcome_context: dict[str, Any] = dataclasses.field(default_factory=dict)
    recorded_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    def __post_init__(self) -> None:
        self.decision.validate()
        if self.decision.company_id != self.company_id:
            raise ValidationError("decision company does not match decision record scope")
        if not self.observation_digest.strip():
            raise ValidationError("observation_digest must not be empty")
        copied_context = _copy_json(self.outcome_context)
        if not isinstance(copied_context, dict):
            raise ValidationError("decision record outcome_context must be a JSON object")
        object.__setattr__(self, "outcome_context", copied_context)
        copied = _copy_json(self.decision.payload)
        validate_decision_record_json(copied, self.outcome_context)
        decision = dataclasses.replace(self.decision, payload=copied)
        if decision.decision_type in {
            DecisionType.PRIORITIZE_TASK,
            DecisionType.ASSIGN_AGENT,
            DecisionType.RETRY_TASK,
            DecisionType.BLOCK_TASK,
            DecisionType.COMPLETE_TASK,
        }:
            raw_task_id = copied.get("task_id")
            if raw_task_id is not None:
                object.__setattr__(decision, "task_id", TaskId.parse(str(raw_task_id)))
        object.__setattr__(self, "decision", decision)

    @staticmethod
    def create(
        *,
        organization_id: OrganizationId,
        company_id: CompanyId,
        decision: Decision,
        observation_digest: str,
        outcome_context: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> DecisionRecord:
        return DecisionRecord(
            id=DecisionRecordId.generate(),
            organization_id=organization_id,
            company_id=company_id,
            decision=decision,
            observation_digest=observation_digest,
            outcome_context={} if outcome_context is None else outcome_context,
            recorded_at=now or utcnow(),
        )


class DecisionRecordRepository(Protocol):
    """Append-only records read under both organization and company scope."""

    async def add(self, record: DecisionRecord) -> None: ...

    async def list_for_company(
        self,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        limit: int = 100,
    ) -> list[DecisionRecord]: ...
