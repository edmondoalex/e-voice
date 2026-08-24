"""Persist Connector compatibility metadata.

Revision ID: 20260824_0012
Revises: 20260820_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0012"
down_revision: str | None = "20260820_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("installations", sa.Column("connector_protocol_version", sa.Integer()))
    op.add_column("installations", sa.Column("connector_capabilities_json", sa.JSON()))
    op.add_column("installations", sa.Column("connector_compatibility_status", sa.String(30)))
    op.add_column("installations", sa.Column("connector_compatibility_reason", sa.String(100)))


def downgrade() -> None:
    op.drop_column("installations", "connector_compatibility_reason")
    op.drop_column("installations", "connector_compatibility_status")
    op.drop_column("installations", "connector_capabilities_json")
    op.drop_column("installations", "connector_protocol_version")
