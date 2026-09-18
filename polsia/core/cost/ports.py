"""UsageRecord entity and the provider-neutral cost/usage ports."""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from typing import Any, Protocol

from polsia.core.runtime.ports import TokenUsage
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import utcnow
from polsia.core.shared.ids import (
    AgentId,
    CompanyId,
    TaskExecutionId,
    TaskId,
    UsageRecordId,
)


@dataclasses.dataclass(frozen=True)
class UsageRecord:
    """One execution's measured usage. Generic and provider-neutral: the OS
    never prices it (a CostCalculator adapter may estimate cost later)."""

    id: UsageRecordId
    company_id: CompanyId
    execution_id: TaskExecutionId
    task_id: TaskId
    agent_id: AgentId | None = None
    runtime: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    duration_seconds: float = 0.0
    estimated_cost: Decimal | None = None  # set by a CostCalculator adapter
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        company_id: CompanyId,
        execution_id: TaskExecutionId,
        task_id: TaskId,
        agent_id: AgentId | None = None,
        runtime: str | None = None,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        tool_calls: int = 0,
        duration_seconds: float = 0.0,
        metadata: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> UsageRecord:
        if prompt_tokens < 0 or completion_tokens < 0 or tool_calls < 0:
            raise ValidationError("usage counts must be non-negative")
        if duration_seconds < 0:
            raise ValidationError("duration_seconds must be non-negative")
        return UsageRecord(
            id=UsageRecordId.generate(),
            company_id=company_id,
            execution_id=execution_id,
            task_id=task_id,
            agent_id=agent_id,
            runtime=runtime,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tool_calls=tool_calls,
            duration_seconds=duration_seconds,
            metadata=dict(metadata or {}),
            created_at=now or utcnow(),
        )

    @staticmethod
    def from_runtime_result(
        *,
        company_id: CompanyId,
        execution_id: TaskExecutionId,
        task_id: TaskId,
        agent_id: AgentId | None,
        runtime: str | None,
        model: str | None,
        usage: TokenUsage,
        tool_call_count: int,
        duration_seconds: float,
        metadata: dict[str, Any] | None = None,
    ) -> UsageRecord:
        """Build a record from a RuntimeResult's generic usage data."""
        return UsageRecord.create(
            company_id=company_id,
            execution_id=execution_id,
            task_id=task_id,
            agent_id=agent_id,
            runtime=runtime,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            tool_calls=tool_call_count,
            duration_seconds=duration_seconds,
            metadata=metadata,
        )


class UsageRepository(Protocol):
    async def add(self, record: UsageRecord) -> None: ...
    async def list_for_company(
        self, company_id: CompanyId, *, limit: int = 100
    ) -> list[UsageRecord]: ...
    async def list_for_execution(
        self, company_id: CompanyId, execution_id: TaskExecutionId
    ) -> list[UsageRecord]: ...


class CostCalculator(Protocol):
    """Port for cost estimation. Provider-neutral: an adapter maps model
    references to pricing; the domain never embeds prices.

    Returns None when the adapter cannot price the model reference."""

    def estimate(self, record: UsageRecord) -> Decimal | None: ...
