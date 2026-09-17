"""Phase 2 slice 3: agents, skills, tools, model_registry

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("model_reference", sa.String(length=255), nullable=True),
        sa.Column("system_instructions", sa.String(length=4000), nullable=True),
        sa.Column("capabilities_list", JSON_DICT, nullable=False),
        sa.Column("configuration", JSON_DICT, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agents"),
        sa.UniqueConstraint("company_id", "name", name="uq_agents_company_id_name"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_agents_company_id_companies"),
    )
    op.create_index("ix_agents_company_id", "agents", ["company_id"])

    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input_schema", JSON_DICT, nullable=False),
        sa.Column("output_schema", JSON_DICT, nullable=False),
        sa.Column("required_capabilities", JSON_DICT, nullable=False),
        sa.Column("required_tools", JSON_DICT, nullable=False),
        sa.Column("required_permissions", JSON_DICT, nullable=False),
        sa.Column("configuration", JSON_DICT, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skills"),
        sa.UniqueConstraint("name", "version", name="uq_skills_name_version"),
    )
    op.create_index("ix_skills_name", "skills", ["name"])

    op.create_table(
        "tools",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input_schema", JSON_DICT, nullable=False),
        sa.Column("output_schema", JSON_DICT, nullable=False),
        sa.Column("capabilities_list", JSON_DICT, nullable=False),
        sa.Column("configuration", JSON_DICT, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tools"),
        sa.UniqueConstraint("name", "version", name="uq_tools_name_version"),
    )
    op.create_index("ix_tools_name", "tools", ["name"])

    op.create_table(
        "model_registry",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("capabilities_list", JSON_DICT, nullable=False),
        sa.Column("configuration", JSON_DICT, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_registry"),
        sa.UniqueConstraint("name", "version", name="uq_model_registry_name_version"),
    )
    op.create_index("ix_model_registry_name", "model_registry", ["name"])


def downgrade() -> None:
    op.drop_index("ix_model_registry_name", table_name="model_registry")
    op.drop_table("model_registry")
    op.drop_index("ix_tools_name", table_name="tools")
    op.drop_table("tools")
    op.drop_index("ix_skills_name", table_name="skills")
    op.drop_table("skills")
    op.drop_index("ix_agents_company_id", table_name="agents")
    op.drop_table("agents")
