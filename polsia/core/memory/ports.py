"""MemoryPort: the provider-neutral abstraction for company-scoped memory.

Implementations live in ``polsia.adapters.persistence`` (SQLAlchemy keyword
search) and as in-memory fakes in tests. Future providers (e.g. pgvector)
implement this port without changes to the core or services.

No ``summarize()`` in Phase 2; the port leaves room for richer providers.
"""

from __future__ import annotations

from typing import Protocol

from polsia.core.memory.entities import MemoryRecord
from polsia.core.shared.ids import CompanyId, MemoryRecordId


class MemoryPort(Protocol):
    """Company-scoped memory storage, retrieval, search and deletion.

    Every operation takes an explicit ``company_id`` — implementations MUST
    never return records from another tenant.
    """

    async def store(self, record: MemoryRecord) -> None: ...

    async def retrieve(
        self, company_id: CompanyId, record_id: MemoryRecordId
    ) -> MemoryRecord | None: ...

    async def search(
        self, company_id: CompanyId, query: str, *, scope: str | None = None, limit: int = 20
    ) -> list[MemoryRecord]: ...

    async def delete(self, company_id: CompanyId, record_id: MemoryRecordId) -> None: ...

    async def list(
        self, company_id: CompanyId, *, scope: str | None = None, limit: int = 100
    ) -> list[MemoryRecord]: ...
