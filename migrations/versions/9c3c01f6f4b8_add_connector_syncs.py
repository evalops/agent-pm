"""add connector syncs table"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c3c01f6f4b8"
down_revision: str | Sequence[str] | None = "56e1a9d2ba59"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_syncs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_connector_syncs_started_at", "connector_syncs", ["started_at"])
    op.create_index("ix_connector_syncs_connector", "connector_syncs", ["connector"])
    op.create_index("ix_connector_syncs_status", "connector_syncs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_connector_syncs_status", table_name="connector_syncs")
    op.drop_index("ix_connector_syncs_connector", table_name="connector_syncs")
    op.drop_index("ix_connector_syncs_started_at", table_name="connector_syncs")
    op.drop_table("connector_syncs")
