"""Usage application service: company-scoped usage records.

Records are written from RuntimeResult's generic usage data by the
orchestration service; the service enforces tenant isolation on reads and
emits ``usage.recorded`` events.
"""

from __future__ import annotations

from decimal import Decimal

from polsia.core.cost.ports import CostCalculator, UsageRecord, UsageRepository
from polsia.core.shared.events import EventBus, EventEnvelope
from polsia.core.shared.ids import CompanyId, OrganizationId, TaskExecutionId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.planning import _CompanyScope


class UsageService:
    """Usage use cases within an explicitly scoped organization + company."""

    def __init__(
        self,
        usage: UsageRepository,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
        cost_calculator: CostCalculator | None = None,
    ) -> None:
        self._usage = usage
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus
        # Optional adapter: pricing stays outside the domain (null in Phase 2).
        self._cost_calculator = cost_calculator

    async def record(
        self, record: UsageRecord, *, organization_id: OrganizationId | None = None
    ) -> UsageRecord:
        """Persist a usage record (already built by the orchestration flow)."""
        if self._cost_calculator is not None:
            estimate = self._cost_calculator.estimate(record)
            if estimate is not None:
                record = _with_cost(record, estimate)
        await self._usage.add(record)
        self._bus.publish(
            EventEnvelope(
                event_type="usage.recorded",
                organization_id=organization_id,
                company_id=record.company_id,
                payload={
                    "usage_id": str(record.id),
                    "task_id": str(record.task_id),
                    "execution_id": str(record.execution_id),
                    "model": record.model,
                    "total_tokens": record.prompt_tokens + record.completion_tokens,
                    "tool_calls": record.tool_calls,
                    "duration_seconds": record.duration_seconds,
                },
            )
        )
        return record

    async def list_usage(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        limit: int = 100,
    ) -> list[UsageRecord]:
        await self._scope.require_company(actor, organization_id, company_id)
        return await self._usage.list_for_company(company_id, limit=limit)

    async def usage_for_execution(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        execution_id: TaskExecutionId,
    ) -> list[UsageRecord]:
        await self._scope.require_company(actor, organization_id, company_id)
        return await self._usage.list_for_execution(company_id, execution_id)


def _with_cost(record: UsageRecord, cost: Decimal) -> UsageRecord:
    import dataclasses

    return dataclasses.replace(record, estimated_cost=cost)
