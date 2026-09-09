"""Persist per-media-player source settings.

Revision ID: 20260909_0019
Revises: 20260907_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0019"
down_revision: str | None = "20260907_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "entities",
        sa.Column("media_source_settings", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("entities", "media_source_settings")
