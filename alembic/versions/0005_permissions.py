"""Phase 2 slice 4: permission policies + approvals

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("principal_type", sa.String(length=100), nullable=False),
        sa.Column("principal_id", sa.String(length=255), nullable=True),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("constraints", JSON_DICT, nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_permissions"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_permissions_company_id_companies"
        ),
    )
    op.create_index("ix_permissions_company_id", "permissions", ["company_id"])
    op.create_index(
        "ix_permissions_company_principal",
        "permissions",
        ["company_id", "principal_type", "principal_id"],
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("requested_by", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=40), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_approvals"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_approvals_company_id_companies"
        ),
    )
    op.create_index("ix_approvals_company_id", "approvals", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_approvals_company_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_permissions_company_principal", table_name="permissions")
    op.drop_index("ix_permissions_company_id", table_name="permissions")
    op.drop_table("permissions")
