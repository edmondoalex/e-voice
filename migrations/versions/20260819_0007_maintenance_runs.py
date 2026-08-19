"""Record automatic database maintenance executions.

Revision ID: 20260819_0007
Revises: 20260819_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_0007"
down_revision: str | None = "20260819_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "maintenance_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("deleted_counts_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(100)),
    )
    op.create_index("ix_maintenance_runs_kind_started", "maintenance_runs", ["kind", "started_at"])


def downgrade() -> None:
    op.drop_table("maintenance_runs")
