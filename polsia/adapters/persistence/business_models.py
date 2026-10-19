"""SQLAlchemy model for tenant-scoped business idea intake persistence."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from polsia.adapters.persistence.models import JSON_DICT, Base


class BusinessIdeaModel(Base):
    __tablename__ = "business_ideas"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("organizations.id", name="fk_business_ideas_organization_id_organizations"),
        nullable=False,
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("users.id", name="fk_business_ideas_owner_user_id_users"),
        nullable=False,
    )
    raw_idea: Mapped[str] = mapped_column(String(8000), nullable=False)
    description: Mapped[str] = mapped_column(String(8000), nullable=False)
    target_market: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    geo_preferences: Mapped[list[Any]] = mapped_column(JSON_DICT, nullable=False)
    budget_constraints: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    additional_constraints: Mapped[list[Any]] = mapped_column(JSON_DICT, nullable=False)
    inferred_requirements: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    intake_status: Mapped[str] = mapped_column(String(20), nullable=False)
    blueprint_status: Mapped[str] = mapped_column(String(20), nullable=False)
    blueprint: Mapped[dict[str, Any] | None] = mapped_column(JSON_DICT, nullable=True)
    blueprint_version: Mapped[int] = mapped_column(nullable=False, default=0)
    submitted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_business_ideas_tenant_time", "organization_id", "submitted_at"),)
