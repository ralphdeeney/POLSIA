"""Phase 2 slice 8: usage_records

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("execution_id", sa.String(length=40), nullable=False),
        sa.Column("task_id", sa.String(length=40), nullable=False),
        sa.Column("agent_id", sa.String(length=40), nullable=True),
        sa.Column("runtime", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("metadata_json", JSON_DICT, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_usage_records"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_usage_records_company_id_companies"
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["task_executions.id"],
            name="fk_usage_records_execution_id_task_executions",
        ),
    )
    op.create_index("ix_usage_records_company_id", "usage_records", ["company_id"])
    op.create_index("ix_usage_records_execution_id", "usage_records", ["execution_id"])
    op.create_index("ix_usage_records_task_id", "usage_records", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_records_task_id", table_name="usage_records")
    op.drop_index("ix_usage_records_execution_id", table_name="usage_records")
    op.drop_index("ix_usage_records_company_id", table_name="usage_records")
    op.drop_table("usage_records")
