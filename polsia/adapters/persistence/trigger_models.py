"""SQLAlchemy models for explicit event-trigger policies and delivery claims."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from polsia.adapters.persistence.models import JSON_DICT, Base


class EventTriggerPolicyModel(Base):
    __tablename__ = "event_trigger_policies"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("organizations.id"), nullable=False
    )
    company_id: Mapped[str] = mapped_column(String(40), ForeignKey("companies.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=false())
    limits: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_event_trigger_policies_scope_event", "organization_id", "company_id", "event_type"
        ),
    )


class TriggerDeliveryModel(Base):
    __tablename__ = "trigger_deliveries"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    policy_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("event_trigger_policies.id"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(40), ForeignKey("events.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(40), nullable=False)
    company_id: Mapped[str] = mapped_column(String(40), nullable=False)
    cycle_id: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Leased, fenced, bounded at-least-once recovery bookkeeping.
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fencing_token: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    __table_args__ = (
        UniqueConstraint("policy_id", "event_id", name="uq_trigger_deliveries_policy_event"),
        Index("ix_trigger_deliveries_tenant_time", "organization_id", "company_id", "created_at"),
        Index("ix_trigger_deliveries_lease", "status", "lease_expires_at"),
    )
