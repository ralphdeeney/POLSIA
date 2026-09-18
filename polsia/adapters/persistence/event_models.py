"""SQLAlchemy ORM model for durable events (Phase 2, ADR-0005)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from polsia.adapters.persistence.models import JSON_DICT, Base


class EventRecordModel(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    aggregate_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    aggregate_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    organization_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    company_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
