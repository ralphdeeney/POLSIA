"""Phase 3 reliability: leased, fenced trigger delivery claims.

Adds recovery bookkeeping to ``trigger_deliveries`` so an interrupted worker
does not permanently strand a ``(policy, event)`` pair:

- ``attempts``          — bounded delivery attempt counter (default 0 for rows
                          created before this migration; the next claim is 1).
- ``lease_owner``       — identity of the worker currently holding the lease.
- ``lease_expires_at``  — lease deadline; after it passes the claim is stealable.
- ``fencing_token``     — monotonic per-pair token; ``finish`` is fenced on it.

Existing rows keep their status and become reclaimable once terminal they are
not; a legacy ``claimed`` row with a NULL ``lease_expires_at`` is treated as an
expired lease and may be recovered.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trigger_deliveries",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "trigger_deliveries",
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "trigger_deliveries",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trigger_deliveries",
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        "ix_trigger_deliveries_lease",
        "trigger_deliveries",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_trigger_deliveries_lease", table_name="trigger_deliveries")
    op.drop_column("trigger_deliveries", "fencing_token")
    op.drop_column("trigger_deliveries", "lease_expires_at")
    op.drop_column("trigger_deliveries", "lease_owner")
    op.drop_column("trigger_deliveries", "attempts")
