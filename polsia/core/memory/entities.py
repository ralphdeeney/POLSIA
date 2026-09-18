"""Memory domain entity (company-scoped, frozen dataclass)."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import utcnow
from polsia.core.shared.ids import CompanyId, MemoryRecordId


def _require_non_empty(value: str, label: str) -> str:
    if not value.strip():
        raise ValidationError(f"{label} must not be empty")
    return value.strip()


def _validate_importance(importance: float) -> float:
    if importance < 0.0 or importance > 1.0:
        raise ValidationError("importance must be between 0.0 and 1.0")
    return float(importance)


@dataclasses.dataclass(frozen=True)
class MemoryRecord:
    """A company-scoped memory record.

    ``scope`` is a free-form string naming what the record belongs to (e.g.
    ``"company"``, ``"agent"``, ``"goal"``, ``"task"``); ``type`` is a
    free-form kind (e.g. ``"observation"``). ``content`` is opaque — the OS
    never interprets its meaning. ``metadata`` is an arbitrary JSON payload.
    """

    id: MemoryRecordId
    company_id: CompanyId
    scope: str
    type: str
    content: str
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    importance: float = 0.5
    created_at: dt.datetime = dataclasses.field(default_factory=utcnow)
    updated_at: dt.datetime = dataclasses.field(default_factory=utcnow)

    @staticmethod
    def create(
        *,
        company_id: CompanyId,
        scope: str,
        type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
        now: dt.datetime | None = None,
    ) -> MemoryRecord:
        ts = now or utcnow()
        return MemoryRecord(
            id=MemoryRecordId.generate(),
            company_id=company_id,
            scope=_require_non_empty(scope, "scope"),
            type=_require_non_empty(type, "type"),
            content=_require_non_empty(content, "content"),
            metadata=dict(metadata or {}),
            importance=_validate_importance(importance),
            created_at=ts,
            updated_at=ts,
        )

    def update(
        self,
        *,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
        importance: float | None = None,
    ) -> MemoryRecord:
        """Return a mutated copy; provided values are validated, the rest keep
        their current value. ``updated_at`` always moves forward."""
        changes: dict[str, Any] = {}
        if content is not None:
            changes["content"] = _require_non_empty(content, "content")
        if metadata is not None:
            changes["metadata"] = dict(metadata)
        if importance is not None:
            changes["importance"] = _validate_importance(importance)
        return dataclasses.replace(self, updated_at=utcnow(), **changes)
