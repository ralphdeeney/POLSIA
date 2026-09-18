"""Memory endpoints (Phase 2, Slice 5).

Company-scoped under /organizations/{org_id}/companies/{company_id}/memory:

- ``POST /memory``: store a memory record (org member required).
- ``GET /memory``: list records (optional scope/limit).
- ``GET /memory/search``: keyword search (query required; always
  company-scoped — never leaks other tenants).
- ``GET /memory/{record_id}``: retrieve one record.
- ``DELETE /memory/{record_id}``: remove a record.

Thin HTTP surface over :class:`polsia.services.memory.MemoryService`. All
authorization (org membership + company scope) happens inside the services.
The content is opaque — the OS never interprets its meaning.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from polsia.api.deps import CurrentUser, MemoryServiceDep, parse_company_id, parse_organization_id
from polsia.api.schemas import MemoryRecordOut, MemoryWrite
from polsia.core.shared.errors import NotFoundError
from polsia.core.shared.ids import MemoryRecordId

memory_router = APIRouter(
    prefix="/organizations/{org_id}/companies/{company_id}/memory", tags=["memory"]
)


@memory_router.post("", status_code=status.HTTP_201_CREATED, response_model=MemoryRecordOut)
async def store_memory(
    org_id: str,
    company_id: str,
    payload: MemoryWrite,
    user: CurrentUser,
    service: MemoryServiceDep,
) -> MemoryRecordOut:
    """Store a memory record (business-agnostic opaque content)."""
    record = await service.write(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        scope=payload.scope,
        type=payload.type,
        content=payload.content,
        metadata=payload.metadata,
        importance=payload.importance,
    )
    return MemoryRecordOut.model_validate(record)


@memory_router.get("", response_model=list[MemoryRecordOut])
async def list_memory(
    org_id: str,
    company_id: str,
    user: CurrentUser,
    service: MemoryServiceDep,
    scope: str | None = None,
    limit: int = 100,
) -> list[MemoryRecordOut]:
    """List memory records, optionally filtered by scope."""
    records = await service.list_records(
        user, parse_organization_id(org_id), parse_company_id(company_id), scope=scope, limit=limit
    )
    return [MemoryRecordOut.model_validate(r) for r in records]


@memory_router.get("/search", response_model=list[MemoryRecordOut])
async def search_memory(
    org_id: str,
    company_id: str,
    user: CurrentUser,
    service: MemoryServiceDep,
    query: str,
    scope: str | None = None,
    limit: int = 20,
) -> list[MemoryRecordOut]:
    """Keyword search over content. Membership is enforced first so memory
    cannot be probed cross-tenant; results are always company-scoped."""
    records = await service.search(
        user,
        parse_organization_id(org_id),
        parse_company_id(company_id),
        query,
        scope=scope,
        limit=limit,
    )
    return [MemoryRecordOut.model_validate(r) for r in records]


@memory_router.get("/{record_id}", response_model=MemoryRecordOut)
async def get_memory(
    org_id: str,
    company_id: str,
    record_id: str,
    user: CurrentUser,
    service: MemoryServiceDep,
) -> MemoryRecordOut:
    """Retrieve a single memory record."""
    record = await service.read(
        user, parse_organization_id(org_id), parse_company_id(company_id), _record_id(record_id)
    )
    return MemoryRecordOut.model_validate(record)


@memory_router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    org_id: str,
    company_id: str,
    record_id: str,
    user: CurrentUser,
    service: MemoryServiceDep,
) -> None:
    """Remove a memory record (missing records are 404s)."""
    await service.remove(
        user, parse_organization_id(org_id), parse_company_id(company_id), _record_id(record_id)
    )


def _record_id(raw: str) -> MemoryRecordId:
    try:
        return MemoryRecordId.parse(raw)
    except ValueError as exc:
        raise NotFoundError(f"memory record {raw!r} not found") from exc
