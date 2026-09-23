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


def _copy_json(value: Any) -> Any:
    """Copy JSON-compatible data through a serialization boundary."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
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
        object.__setattr__(self, "outcome_context", _copy_json(self.outcome_context))
        copied = _copy_json(self.decision.payload)
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
            outcome_context=dict(outcome_context or {}),
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
