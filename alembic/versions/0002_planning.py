"""Phase 2 slice 1: goals, kpis

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("parent_goal_id", sa.String(length=40), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", JSON_DICT, nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_goals"),
        sa.UniqueConstraint("company_id", "title", name="uq_goals_company_id_title"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_goals_company_id_companies"
        ),
    )
    op.create_index("ix_goals_company_id", "goals", ["company_id"])

    op.create_table(
        "kpis",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("goal_id", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("current_value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("metadata_json", JSON_DICT, nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_kpis"),
        sa.UniqueConstraint("company_id", "name", name="uq_kpis_company_id_name"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_kpis_company_id_companies"
        ),
    )
    op.create_index("ix_kpis_company_id", "kpis", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_kpis_company_id", table_name="kpis")
    op.drop_table("kpis")
    op.drop_index("ix_goals_company_id", table_name="goals")
    op.drop_table("goals")
