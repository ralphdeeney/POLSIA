"""SQLAlchemy model for durable CEO decision records."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from polsia.adapters.persistence.models import JSON_DICT, Base


class DecisionRecordModel(Base):
    __tablename__ = "decision_records"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("organizations.id", name="fk_decision_records_organization_id_organizations"),
        nullable=False,
    )
    company_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("companies.id", name="fk_decision_records_company_id_companies"),
        nullable=False,
    )
    decision_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    rationale: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cycle_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decision_created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observation_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome_context: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_decision_records_tenant_time", "organization_id", "company_id", "recorded_at"),
    )
