"""Phase 3 slice 6: append-only CEO decision records.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "decision_records",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("organization_id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("decision_type", sa.String(length=40), nullable=False),
        sa.Column("payload", JSON_DICT, nullable=False),
        sa.Column("rationale", sa.String(length=2000), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model_reference", sa.String(length=255), nullable=True),
        sa.Column("cycle_id", sa.String(length=40), nullable=True),
        sa.Column("task_id", sa.String(length=40), nullable=True),
        sa.Column("decision_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_digest", sa.String(length=128), nullable=False),
        sa.Column("outcome_context", JSON_DICT, nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_decision_records"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_decision_records_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_decision_records_company_id_companies"
        ),
    )
    op.create_index(
        "ix_decision_records_tenant_time",
        "decision_records",
        ["organization_id", "company_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_decision_records_tenant_time", table_name="decision_records")
    op.drop_table("decision_records")
