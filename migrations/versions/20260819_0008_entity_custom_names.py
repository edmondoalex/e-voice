"""Add entity display and voice naming overrides.

Revision ID: 20260819_0008
Revises: 20260819_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_0008"
down_revision: str | None = "20260819_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("entities", sa.Column("display_name", sa.String(120)))
    op.add_column("entities", sa.Column("voice_name", sa.String(120)))
    op.add_column(
        "entities", sa.Column("voice_aliases", sa.JSON(), nullable=False, server_default="[]")
    )


def downgrade() -> None:
    op.drop_column("entities", "voice_aliases")
    op.drop_column("entities", "voice_name")
    op.drop_column("entities", "display_name")
