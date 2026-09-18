"""Phase 2 slice 5: memory records

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "memory_records",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("content", sa.String(length=8000), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_memory_records"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_memory_records_company_id_companies"
        ),
    )
    op.create_index("ix_memory_records_company_id", "memory_records", ["company_id"])
    op.create_index("ix_memory_records_scope", "memory_records", ["scope"])
    op.create_index(
        "ix_memory_records_company_scope", "memory_records", ["company_id", "scope"]
    )


def downgrade() -> None:
    op.drop_index("ix_memory_records_company_scope", table_name="memory_records")
    op.drop_index("ix_memory_records_scope", table_name="memory_records")
    op.drop_index("ix_memory_records_company_id", table_name="memory_records")
    op.drop_table("memory_records")
