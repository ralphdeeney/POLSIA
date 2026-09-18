"""Memory application service: company-scoped memory use cases.

Every operation takes an explicit organization scope, verifies the actor's
org membership, and verifies the target company belongs to that organization
(via the shared :class:`~polsia.services.planning._CompanyScope` helper).
Search is always company-scoped — tenant isolation: records from other
tenants are never returned. Events follow ADR-0004.
"""

from __future__ import annotations

from typing import Any

from polsia.core.memory.entities import MemoryRecord
from polsia.core.memory.ports import MemoryPort
from polsia.core.shared.errors import NotFoundError
from polsia.core.shared.events import EventBus, EventEnvelope
from polsia.core.shared.ids import CompanyId, MemoryRecordId, OrganizationId
from polsia.core.tenancy.entities import User
from polsia.core.tenancy.ports import CompanyRepository, MembershipRepository
from polsia.services.planning import _CompanyScope


class MemoryService:
    """Memory use cases within an explicitly scoped organization + company."""

    def __init__(
        self,
        memory: MemoryPort,
        companies: CompanyRepository,
        memberships: MembershipRepository,
        bus: EventBus,
    ) -> None:
        self._memory = memory
        self._scope = _CompanyScope(companies, memberships)
        self._bus = bus

    async def write(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        scope: str,
        type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
    ) -> MemoryRecord:
        await self._scope.require_company(actor, organization_id, company_id)
        record = MemoryRecord.create(
            company_id=company_id,
            scope=scope,
            type=type,
            content=content,
            metadata=metadata,
            importance=importance,
        )
        await self._memory.store(record)
        self._publish(
            "memory.created",
            organization_id=organization_id,
            company_id=company_id,
            record_id=record.id,
            payload={"scope": record.scope, "type": record.type},
        )
        return record

    async def read(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        record_id: MemoryRecordId,
    ) -> MemoryRecord:
        await self._scope.require_company(actor, organization_id, company_id)
        record = await self._memory.retrieve(company_id, record_id)
        if record is None:
            raise NotFoundError(f"memory record {record_id} not found")
        return record

    async def search(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        query: str,
        *,
        scope: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]:
        await self._scope.require_company(actor, organization_id, company_id)
        return await self._memory.search(company_id, query, scope=scope, limit=limit)

    async def remove(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        record_id: MemoryRecordId,
    ) -> None:
        await self._scope.require_company(actor, organization_id, company_id)
        record = await self._memory.retrieve(company_id, record_id)
        if record is None:
            raise NotFoundError(f"memory record {record_id} not found")
        await self._memory.delete(company_id, record_id)
        self._publish(
            "memory.deleted",
            organization_id=organization_id,
            company_id=company_id,
            record_id=record.id,
            payload={"scope": record.scope, "type": record.type},
        )

    async def list_records(
        self,
        actor: User,
        organization_id: OrganizationId,
        company_id: CompanyId,
        *,
        scope: str | None = None,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        await self._scope.require_company(actor, organization_id, company_id)
        return await self._memory.list(company_id, scope=scope, limit=limit)

    def _publish(
        self,
        event_type: str,
        *,
        organization_id: OrganizationId,
        company_id: CompanyId,
        record_id: MemoryRecordId,
        payload: dict[str, Any],
    ) -> None:
        self._bus.publish(
            EventEnvelope(
                event_type=event_type,
                organization_id=organization_id,
                company_id=company_id,
                payload={"memory_record_id": str(record_id), **payload},
            )
        )
