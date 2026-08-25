"""Persist per-entity Alexa device type overrides.

Revision ID: 20260825_0013
Revises: 20260824_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0013"
down_revision: str | None = "20260824_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("entities", sa.Column("alexa_device_type", sa.String(32)))


def downgrade() -> None:
    op.drop_column("entities", "alexa_device_type")
