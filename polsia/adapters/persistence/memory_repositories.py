"""SQLAlchemy implementation of the MemoryPort.

Same contract as repositories.py: no commits (the session provider owns the
transaction boundary), IntegrityError translated into domain errors, and
``company_id`` in every WHERE clause (tenant isolation). Search is a simple,
parameterized case-insensitive keyword search over content — no vector DB,
embeddings, or RAG.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import ColumnElement, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.memory_models import MemoryRecordModel
from polsia.adapters.persistence.translate import translate_integrity_error
from polsia.core.memory.entities import MemoryRecord
from polsia.core.shared.ids import CompanyId, MemoryRecordId

_MAX_LIMIT = 1000
_MIN_LIMIT = 1


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite stores timestamps without tzinfo; re-anchor naive values to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


def _clamp(limit: int) -> int:
    return max(_MIN_LIMIT, min(limit, _MAX_LIMIT))


def _to_record(row: MemoryRecordModel) -> MemoryRecord:
    return MemoryRecord(
        id=MemoryRecordId.parse(row.id),
        company_id=CompanyId.parse(row.company_id),
        scope=row.scope,
        type=row.type,
        content=row.content,
        metadata=dict(row.metadata_json or {}),
        importance=row.importance,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


class SqlAlchemyMemoryStore:
    """Keyword-search :class:`~polsia.core.memory.ports.MemoryPort` backed by
    the ``memory_records`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def store(self, record: MemoryRecord) -> None:
        row = MemoryRecordModel(
            id=str(record.id),
            company_id=str(record.company_id),
            scope=record.scope,
            type=record.type,
            content=record.content,
            importance=record.importance,
            metadata_json=dict(record.metadata),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def retrieve(
        self, company_id: CompanyId, record_id: MemoryRecordId
    ) -> MemoryRecord | None:
        stmt = select(MemoryRecordModel).where(
            MemoryRecordModel.id == str(record_id),
            MemoryRecordModel.company_id == str(company_id),
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_record(row) if row is not None else None

    async def search(
        self, company_id: CompanyId, query: str, *, scope: str | None = None, limit: int = 20
    ) -> list[MemoryRecord]:
        """Keyword search: case-insensitive LIKE over content, one clause per
        whitespace-separated term (all terms must match). Terms are passed as
        bound parameters — never interpolated into the SQL statement.
        ``company_id`` is always in the WHERE (tenant isolation)."""
        terms = [term for term in query.split() if term]
        if not terms:
            return []
        filters: list[ColumnElement[bool]] = [
            MemoryRecordModel.company_id == str(company_id),
            *(func.lower(MemoryRecordModel.content).like(f"%{term.lower()}%") for term in terms),
        ]
        if scope is not None:
            filters.append(MemoryRecordModel.scope == scope)
        stmt = (
            select(MemoryRecordModel)
            .where(*filters)
            .order_by(MemoryRecordModel.created_at, MemoryRecordModel.id)
            .limit(_clamp(limit))
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_record(row) for row in rows]

    async def delete(self, company_id: CompanyId, record_id: MemoryRecordId) -> None:
        """Delete the row. Idempotent: deleting a missing row is a no-op."""
        stmt = sa_delete(MemoryRecordModel).where(
            MemoryRecordModel.id == str(record_id),
            MemoryRecordModel.company_id == str(company_id),
        )
        await self._session.execute(stmt)

    async def list(
        self, company_id: CompanyId, *, scope: str | None = None, limit: int = 100
    ) -> list[MemoryRecord]:
        filters: list[ColumnElement[bool]] = [MemoryRecordModel.company_id == str(company_id)]
        if scope is not None:
            filters.append(MemoryRecordModel.scope == scope)
        stmt = (
            select(MemoryRecordModel)
            .where(*filters)
            .order_by(MemoryRecordModel.created_at, MemoryRecordModel.id)
            .limit(_clamp(limit))
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_record(row) for row in rows]
