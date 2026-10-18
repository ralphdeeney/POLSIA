"""Phase 3 slices 7-8: event trigger policies and idempotent deliveries.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "event_trigger_policies",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("organization_id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("limits", JSON_DICT, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_trigger_policies"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
    )
    op.create_index(
        "ix_event_trigger_policies_scope_event",
        "event_trigger_policies",
        ["organization_id", "company_id", "event_type"],
    )
    op.create_table(
        "trigger_deliveries",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("policy_id", sa.String(length=40), nullable=False),
        sa.Column("event_id", sa.String(length=40), nullable=False),
        sa.Column("organization_id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("cycle_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("detail", JSON_DICT, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_trigger_deliveries"),
        sa.UniqueConstraint("policy_id", "event_id", name="uq_trigger_deliveries_policy_event"),
        sa.ForeignKeyConstraint(["policy_id"], ["event_trigger_policies.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
    )
    op.create_index(
        "ix_trigger_deliveries_tenant_time",
        "trigger_deliveries",
        ["organization_id", "company_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_trigger_deliveries_tenant_time", table_name="trigger_deliveries")
    op.drop_table("trigger_deliveries")
    op.drop_index("ix_event_trigger_policies_scope_event", table_name="event_trigger_policies")
    op.drop_table("event_trigger_policies")
