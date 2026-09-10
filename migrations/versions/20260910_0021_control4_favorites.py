"""Store per-installation Control4 favorite configuration.

Revision ID: 20260910_0021
Revises: 20260910_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0021"
down_revision: str | None = "20260910_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "installations",
        sa.Column("control4_favorites_json", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("installations", "control4_favorites_json")
