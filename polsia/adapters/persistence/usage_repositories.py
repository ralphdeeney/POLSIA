"""SQLAlchemy ORM model + repository for usage records (Phase 2)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from polsia.adapters.persistence.models import JSON_DICT, Base
from polsia.core.cost.ports import UsageRecord
from polsia.core.shared.ids import AgentId, CompanyId, TaskExecutionId, TaskId, UsageRecordId


class UsageRecordModel(Base):
    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("companies.id", name="fk_usage_records_company_id_companies"),
        nullable=False,
        index=True,
    )
    execution_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("task_executions.id", name="fk_usage_records_execution_id_task_executions"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    runtime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _aware(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


def _to_record(row: UsageRecordModel) -> UsageRecord:
    return UsageRecord(
        id=UsageRecordId.parse(row.id),
        company_id=CompanyId.parse(row.company_id),
        execution_id=TaskExecutionId.parse(row.execution_id),
        task_id=TaskId.parse(row.task_id),
        agent_id=AgentId.parse(row.agent_id) if row.agent_id else None,
        runtime=row.runtime,
        model=row.model,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        tool_calls=row.tool_calls,
        duration_seconds=row.duration_seconds,
        estimated_cost=Decimal(str(row.estimated_cost)) if row.estimated_cost is not None else None,
        metadata=dict(row.metadata_json or {}),
        created_at=_aware(row.created_at),
    )


class SqlAlchemyUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: UsageRecord) -> None:
        row = UsageRecordModel(
            id=str(record.id),
            company_id=str(record.company_id),
            execution_id=str(record.execution_id),
            task_id=str(record.task_id),
            agent_id=str(record.agent_id) if record.agent_id else None,
            runtime=record.runtime,
            model=record.model,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            tool_calls=record.tool_calls,
            duration_seconds=record.duration_seconds,
            estimated_cost=record.estimated_cost,
            metadata_json=dict(record.metadata),
            created_at=record.created_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def list_for_company(
        self, company_id: CompanyId, *, limit: int = 100
    ) -> list[UsageRecord]:
        stmt = (
            select(UsageRecordModel)
            .where(UsageRecordModel.company_id == str(company_id))
            .order_by(UsageRecordModel.created_at.desc(), UsageRecordModel.id.desc())
            .limit(max(1, min(limit, 1000)))
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_record(row) for row in rows]

    async def list_for_execution(
        self, company_id: CompanyId, execution_id: TaskExecutionId
    ) -> list[UsageRecord]:
        stmt = select(UsageRecordModel).where(
            UsageRecordModel.company_id == str(company_id),
            UsageRecordModel.execution_id == str(execution_id),
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_record(row) for row in rows]
