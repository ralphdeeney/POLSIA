"""SQLAlchemy 2.0 ORM models for the tenancy domain.

Mapping rules (portable across SQLite and PostgreSQL):
- IDs are `String(40)` text (`<prefix>_<ULID>`), primary keys.
- Enums are `String(20)` storing the StrEnum `.value`.
- Timestamps are `DateTime(timezone=True)` with a portable server default.
- JSON dicts use `sa.JSON` with a `postgresql.JSONB` variant.
- Unique constraints carry explicit, stable names so the repository layer can
  translate integrity failures into precise domain errors.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    MetaData,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Portable JSON column type: plain JSON on SQLite, JSONB on PostgreSQL.
JSON_DICT: JSON = JSON().with_variant(postgresql.JSONB(), "postgresql")

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)


class OrganizationModel(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("users.id", name="fk_organizations_owner_user_id_users"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("slug", name="uq_organizations_slug"),)


class OrgMembershipModel(Base):
    __tablename__ = "org_memberships"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("organizations.id", name="fk_org_memberships_organization_id_organizations"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("users.id", name="fk_org_memberships_user_id_users"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_org_memberships_organization_id_user_id"
        ),
    )


class CompanyModel(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("organizations.id", name="fk_companies_organization_id_organizations"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    identity: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    mission: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    strategy: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_companies_organization_id_slug"),
    )


# -- Phase 2: planning (goals, KPIs) -------------------------------------------


class GoalModel(Base):
    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("companies.id", name="fk_goals_company_id_companies"),
        nullable=False,
        index=True,
    )
    parent_goal_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("company_id", "title", name="uq_goals_company_id_title"),)


class KpiModel(Base):
    __tablename__ = "kpis"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("companies.id", name="fk_kpis_company_id_companies"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_kpis_company_id_name"),)


# -- Phase 2: tasks, dependencies, executions ----------------------------------


class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("companies.id", name="fk_tasks_company_id_companies"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    assigned_agent_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    assigned_skill: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DICT, nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3)
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskDependencyModel(Base):
    __tablename__ = "task_dependencies"

    task_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("tasks.id", name="fk_task_dependencies_task_id_tasks"),
        primary_key=True,
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("tasks.id", name="fk_task_dependencies_depends_on_task_id_tasks"),
        primary_key=True,
    )
    company_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("companies.id", name="fk_task_dependencies_company_id_companies"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "task_id", "depends_on_task_id", name="uq_task_dependencies_task_id_depends_on"
        ),
    )


class TaskExecutionModel(Base):
    __tablename__ = "task_executions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("companies.id", name="fk_task_executions_company_id_companies"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("tasks.id", name="fk_task_executions_task_id_tasks"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    skill_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    runtime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DICT, nullable=True)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DICT, nullable=False)
