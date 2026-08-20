"""Persist Home Assistant entity icons.

Revision ID: 20260820_0010
Revises: 20260820_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0010"
down_revision: str | None = "20260820_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("entities", sa.Column("icon", sa.String(255)))


def downgrade() -> None:
    op.drop_column("entities", "icon")
