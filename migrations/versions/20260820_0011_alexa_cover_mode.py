"""Add per-entity Alexa cover exposure mode.

Revision ID: 20260820_0011
Revises: 20260820_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0011"
down_revision: str | None = "20260820_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("entities", sa.Column("alexa_cover_mode", sa.String(20)))


def downgrade() -> None:
    op.drop_column("entities", "alexa_cover_mode")
