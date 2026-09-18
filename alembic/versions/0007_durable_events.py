"""Phase 2 slice 7: durable events table

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=True),
        sa.Column("aggregate_id", sa.String(length=40), nullable=True),
        sa.Column("organization_id", sa.String(length=40), nullable=True),
        sa.Column("company_id", sa.String(length=40), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("payload", JSON_DICT, nullable=False),
        sa.Column("metadata_json", JSON_DICT, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
    )
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_aggregate_id", "events", ["aggregate_id"])
    op.create_index("ix_events_organization_id", "events", ["organization_id"])
    op.create_index("ix_events_company_id", "events", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_events_company_id", table_name="events")
    op.drop_index("ix_events_organization_id", table_name="events")
    op.drop_index("ix_events_aggregate_id", table_name="events")
    op.drop_index("ix_events_event_type", table_name="events")
    op.drop_table("events")
