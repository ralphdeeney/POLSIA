"""SQLAlchemy implementations of the permission-domain repository ports.

Same contract as repositories.py: no commits (the session provider owns the
transaction boundary), IntegrityError translated into domain errors,
company-scoped lookups, and defense-in-depth company checks on update.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from polsia.adapters.persistence.permission_models import ApprovalModel, PermissionModel
from polsia.adapters.persistence.translate import translate_integrity_error
from polsia.core.permissions.entities import Approval, Permission
from polsia.core.permissions.values import ApprovalStatus, PermissionDecision
from polsia.core.shared.errors import NotFoundError
from polsia.core.shared.ids import ApprovalId, CompanyId, PermissionId, UserId


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite stores timestamps without tzinfo; re-anchor naive values to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


# -- Permission (company-scoped) -------------------------------------------------


def _to_permission(row: PermissionModel) -> Permission:
    return Permission(
        id=PermissionId.parse(row.id),
        company_id=CompanyId.parse(row.company_id),
        principal_type=row.principal_type,
        principal_id=row.principal_id,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        action=row.action,
        decision=PermissionDecision(row.decision),
        constraints=dict(row.constraints or {}),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


class SqlAlchemyPermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, permission: Permission) -> None:
        row = PermissionModel(
            id=str(permission.id),
            company_id=str(permission.company_id),
            principal_type=permission.principal_type,
            principal_id=permission.principal_id,
            resource_type=permission.resource_type,
            resource_id=permission.resource_id,
            action=permission.action,
            decision=permission.decision.value,
            constraints=dict(permission.constraints),
            created_at=permission.created_at,
            updated_at=permission.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, company_id: CompanyId, permission_id: PermissionId) -> Permission | None:
        stmt = select(PermissionModel).where(
            PermissionModel.id == str(permission_id),
            PermissionModel.company_id == str(company_id),
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_permission(row) if row is not None else None

    async def list_for_company(self, company_id: CompanyId) -> list[Permission]:
        stmt = (
            select(PermissionModel)
            .where(PermissionModel.company_id == str(company_id))
            .order_by(PermissionModel.created_at, PermissionModel.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_permission(row) for row in rows]

    async def list_for_principal(
        self, company_id: CompanyId, principal_type: str, principal_id: str | None
    ) -> list[Permission]:
        stmt = (
            select(PermissionModel)
            .where(
                PermissionModel.company_id == str(company_id),
                PermissionModel.principal_type == principal_type,
                PermissionModel.principal_id == principal_id
                if principal_id is not None
                else PermissionModel.principal_id.is_(None),
            )
            .order_by(PermissionModel.created_at, PermissionModel.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_permission(row) for row in rows]

    async def update(self, permission: Permission) -> None:
        row = await self._session.get(PermissionModel, str(permission.id))
        if row is None or row.company_id != str(permission.company_id):
            raise NotFoundError(f"permission {permission.id} not found")
        row.decision = permission.decision.value
        row.constraints = dict(permission.constraints)
        row.updated_at = permission.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc


# -- Approval (company-scoped) -----------------------------------------------------


def _to_approval(row: ApprovalModel) -> Approval:
    return Approval(
        id=ApprovalId.parse(row.id),
        company_id=CompanyId.parse(row.company_id),
        requested_by=UserId.parse(row.requested_by),
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        reason=row.reason,
        status=ApprovalStatus(row.status),
        expires_at=_aware(row.expires_at) if row.expires_at is not None else None,
        decided_by=UserId.parse(row.decided_by) if row.decided_by else None,
        decided_at=_aware(row.decided_at) if row.decided_at is not None else None,
        metadata=dict(row.metadata_json or {}),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


class SqlAlchemyApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, approval: Approval) -> None:
        row = ApprovalModel(
            id=str(approval.id),
            company_id=str(approval.company_id),
            requested_by=str(approval.requested_by),
            action=approval.action,
            resource_type=approval.resource_type,
            resource_id=approval.resource_id,
            reason=approval.reason,
            status=approval.status.value,
            expires_at=approval.expires_at,
            decided_by=str(approval.decided_by) if approval.decided_by else None,
            decided_at=approval.decided_at,
            metadata_json=dict(approval.metadata),
            created_at=approval.created_at,
            updated_at=approval.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

    async def get(self, company_id: CompanyId, approval_id: ApprovalId) -> Approval | None:
        stmt = select(ApprovalModel).where(
            ApprovalModel.id == str(approval_id),
            ApprovalModel.company_id == str(company_id),
        )
        row = (await self._session.scalars(stmt)).first()
        return _to_approval(row) if row is not None else None

    async def list_for_company(self, company_id: CompanyId) -> list[Approval]:
        stmt = (
            select(ApprovalModel)
            .where(ApprovalModel.company_id == str(company_id))
            .order_by(ApprovalModel.created_at, ApprovalModel.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_approval(row) for row in rows]

    async def list_pending(self, company_id: CompanyId) -> list[Approval]:
        stmt = (
            select(ApprovalModel)
            .where(
                ApprovalModel.company_id == str(company_id),
                ApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .order_by(ApprovalModel.created_at, ApprovalModel.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [_to_approval(row) for row in rows]

    async def update(self, approval: Approval) -> None:
        row = await self._session.get(ApprovalModel, str(approval.id))
        if row is None or row.company_id != str(approval.company_id):
            raise NotFoundError(f"approval {approval.id} not found")
        row.status = approval.status.value
        row.decided_by = str(approval.decided_by) if approval.decided_by else None
        row.decided_at = approval.decided_at
        row.updated_at = approval.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc
