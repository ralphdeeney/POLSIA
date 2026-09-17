"""Repository ports for the permissions domain.

Implementations live in `polsia.adapters.persistence` (SQLAlchemy) and as
in-memory fakes in tests. Adapter failures MUST be translated into the domain
errors from `polsia.core.shared.errors` before crossing the boundary.

Scoping: permissions and approvals are company-scoped — the company id is an
explicit mandatory parameter on every port method.
"""

from __future__ import annotations

from typing import Protocol

from polsia.core.permissions.entities import Approval, Permission
from polsia.core.shared.ids import ApprovalId, CompanyId, PermissionId


class PermissionRepository(Protocol):
    async def add(self, permission: Permission) -> None: ...
    async def get(
        self, company_id: CompanyId, permission_id: PermissionId
    ) -> Permission | None: ...
    async def list_for_company(self, company_id: CompanyId) -> list[Permission]: ...
    async def list_for_principal(
        self, company_id: CompanyId, principal_type: str, principal_id: str | None
    ) -> list[Permission]: ...
    async def update(self, permission: Permission) -> None: ...


class ApprovalRepository(Protocol):
    async def add(self, approval: Approval) -> None: ...
    async def get(self, company_id: CompanyId, approval_id: ApprovalId) -> Approval | None: ...
    async def list_for_company(self, company_id: CompanyId) -> list[Approval]: ...
    async def list_pending(self, company_id: CompanyId) -> list[Approval]: ...
    async def update(self, approval: Approval) -> None: ...
