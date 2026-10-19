"""Phase 4 wave 3: tenant-scoped business idea intake.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "business_ideas",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("organization_id", sa.String(length=40), nullable=False),
        sa.Column("owner_user_id", sa.String(length=40), nullable=False),
        sa.Column("raw_idea", sa.String(length=8000), nullable=False),
        sa.Column("description", sa.String(length=8000), nullable=False),
        sa.Column("target_market", sa.String(length=2000), nullable=True),
        sa.Column("industry", sa.String(length=255), nullable=True),
        sa.Column("geo_preferences", JSON_DICT, nullable=False),
        sa.Column("budget_constraints", sa.String(length=2000), nullable=True),
        sa.Column("additional_constraints", JSON_DICT, nullable=False),
        sa.Column("inferred_requirements", JSON_DICT, nullable=False),
        sa.Column("intake_status", sa.String(length=20), nullable=False),
        sa.Column("blueprint_status", sa.String(length=20), nullable=False),
        sa.Column("blueprint", JSON_DICT, nullable=True),
        sa.Column("blueprint_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "submitted_at",
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
        sa.PrimaryKeyConstraint("id", name="pk_business_ideas"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_business_ideas_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_business_ideas_owner_user_id_users"
        ),
    )
    op.create_index(
        "ix_business_ideas_tenant_time",
        "business_ideas",
        ["organization_id", "submitted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_business_ideas_tenant_time", table_name="business_ideas")
    op.drop_table("business_ideas")
