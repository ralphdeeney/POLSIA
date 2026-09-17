"""SQLAlchemy ORM models for the registry domain (Phase 2).

Separate module, same declarative Base (shared metadata) as models.py so
`create_all_metadata` and Alembic cover these tables.
Scoping (decision C2): agents are company-scoped; skills/tools/models are
platform-global, unique by (name, version).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from polsia.adapters.persistence.models import JSON_DICT, Base


class AgentModel(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("companies.id", name="fk_agents_company_id_companies"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    model_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    system_instructions: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    capabilities_list: Mapped[list[str]] = mapped_column(JSON_DICT, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_agents_company_id_name"),)


class SkillModel(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    required_capabilities: Mapped[list[str]] = mapped_column(JSON_DICT, nullable=False)
    required_tools: Mapped[list[str]] = mapped_column(JSON_DICT, nullable=False)
    required_permissions: Mapped[list[str]] = mapped_column(JSON_DICT, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("name", "version", name="uq_skills_name_version"),)


class ToolModel(Base):
    __tablename__ = "tools"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    capabilities_list: Mapped[list[str]] = mapped_column(JSON_DICT, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("name", "version", name="uq_tools_name_version"),)


class ModelRegistryModel(Base):
    __tablename__ = "model_registry"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    capabilities_list: Mapped[list[str]] = mapped_column(JSON_DICT, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("name", "version", name="uq_model_registry_name_version"),)
