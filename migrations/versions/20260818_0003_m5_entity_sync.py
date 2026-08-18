"""Add stable HA identity and synchronization metadata.

Revision ID: 20260818_0003
Revises: 20260817_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0003"
down_revision: str | None = "20260817_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("installations") as batch:
        batch.add_column(
            sa.Column("sync_revision", sa.BigInteger(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("inventory_synced_at", sa.DateTime(timezone=True)))
    with op.batch_alter_table("entities") as batch:
        batch.add_column(sa.Column("ha_registry_id", sa.String(64)))
        batch.add_column(sa.Column("device_id", sa.String(64)))
        batch.add_column(sa.Column("device_name", sa.String(255)))
        batch.create_unique_constraint(
            "uq_entities_installation_registry", ["installation_id", "ha_registry_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("entities") as batch:
        batch.drop_constraint("uq_entities_installation_registry", type_="unique")
        batch.drop_column("device_name")
        batch.drop_column("device_id")
        batch.drop_column("ha_registry_id")
    with op.batch_alter_table("installations") as batch:
        batch.drop_column("inventory_synced_at")
        batch.drop_column("sync_revision")
