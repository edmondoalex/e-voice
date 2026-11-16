"""Add anonymized Wear claim attempt limits.

Revision ID: 20261116_0026
Revises: 20261116_0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261116_0026"
down_revision: str | None = "20261116_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wear_claim_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("successful", sa.Boolean(), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wear_claim_attempts_source_time",
        "wear_claim_attempts",
        ["source_hash", "attempted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_wear_claim_attempts_source_time", table_name="wear_claim_attempts")
    op.drop_table("wear_claim_attempts")
