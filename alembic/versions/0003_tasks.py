"""Phase 2 slice 2: tasks, task_dependencies, task_executions

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

JSON_DICT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("goal_id", sa.String(length=40), nullable=True),
        sa.Column("parent_task_id", sa.String(length=40), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("assigned_agent_id", sa.String(length=40), nullable=True),
        sa.Column("assigned_skill", sa.String(length=255), nullable=True),
        sa.Column("input_json", JSON_DICT, nullable=False),
        sa.Column("output_json", JSON_DICT, nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_tasks_company_id_companies"),
    )
    op.create_index("ix_tasks_company_id", "tasks", ["company_id"])
    op.create_index("ix_tasks_goal_id", "tasks", ["goal_id"])

    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.String(length=40), nullable=False),
        sa.Column("depends_on_task_id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "task_id", "depends_on_task_id", name="pk_task_dependencies"
        ),
        sa.UniqueConstraint(
            "task_id", "depends_on_task_id", name="uq_task_dependencies_task_id_depends_on"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="fk_task_dependencies_task_id_tasks"
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_task_id"], ["tasks.id"], name="fk_task_dependencies_depends_on_task_id_tasks"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_task_dependencies_company_id_companies"
        ),
    )
    op.create_index("ix_task_dependencies_company_id", "task_dependencies", ["company_id"])

    op.create_table(
        "task_executions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("task_id", sa.String(length=40), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("agent_id", sa.String(length=40), nullable=True),
        sa.Column("skill_id", sa.String(length=255), nullable=True),
        sa.Column("runtime", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("input_json", JSON_DICT, nullable=False),
        sa.Column("output_json", JSON_DICT, nullable=True),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", JSON_DICT, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_executions"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_task_executions_company_id_companies"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_task_executions_task_id_tasks"),
    )
    op.create_index("ix_task_executions_company_id", "task_executions", ["company_id"])
    op.create_index("ix_task_executions_task_id", "task_executions", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_executions_task_id", table_name="task_executions")
    op.drop_index("ix_task_executions_company_id", table_name="task_executions")
    op.drop_table("task_executions")
    op.drop_index("ix_task_dependencies_company_id", table_name="task_dependencies")
    op.drop_table("task_dependencies")
    op.drop_index("ix_tasks_goal_id", table_name="tasks")
    op.drop_index("ix_tasks_company_id", table_name="tasks")
    op.drop_table("tasks")
