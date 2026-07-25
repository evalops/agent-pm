"""drop unused tasks, plans, and feedback tables

Revision ID: 3f7a2c9e1d84
Revises: 9c3c01f6f4b8
Create Date: 2026-07-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f7a2c9e1d84"
down_revision: str | Sequence[str] | None = "9c3c01f6f4b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f("ix_tasks_task_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_created_at"), table_name="tasks")
    op.drop_table("tasks")
    op.drop_index(op.f("ix_plans_plan_id"), table_name="plans")
    op.drop_index(op.f("ix_plans_created_at"), table_name="plans")
    op.drop_table("plans")
    op.drop_index(op.f("ix_feedback_plan_id"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_created_at"), table_name="feedback")
    op.drop_table("feedback")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("user", sa.String(length=255), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("edited_prd", sa.Text(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_feedback_created_at"), "feedback", ["created_at"], unique=False)
    op.create_index(op.f("ix_feedback_plan_id"), "feedback", ["plan_id"], unique=False)
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("prd_markdown", sa.Text(), nullable=True),
        sa.Column("ticket_plan", sa.JSON(), nullable=True),
        sa.Column("status_digest", sa.Text(), nullable=True),
        sa.Column("critic_review", sa.JSON(), nullable=True),
        sa.Column("revision_history", sa.JSON(), nullable=False),
        sa.Column("trace_name", sa.String(length=255), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plans_created_at"), "plans", ["created_at"], unique=False)
    op.create_index(op.f("ix_plans_plan_id"), "plans", ["plan_id"], unique=True)
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "RUNNING", "COMPLETED", "FAILED", "RETRYING", name="taskstatusdb"),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_created_at"), "tasks", ["created_at"], unique=False)
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)
    op.create_index(op.f("ix_tasks_task_id"), "tasks", ["task_id"], unique=True)
