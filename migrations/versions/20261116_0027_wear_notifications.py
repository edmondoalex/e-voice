"""Add per-device Wear notification delivery.

Revision ID: 20261116_0027
Revises: 20261116_0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261116_0027"
down_revision: str | None = "20261116_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wear_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(120), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["wear_devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["installation_id", "tenant_id"],
            ["installations.id", "installations.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "event_key", name="uq_wear_notification_device_event"),
    )
    op.create_index(
        "ix_wear_notifications_pending",
        "wear_notifications",
        ["device_id", "acknowledged_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_wear_notifications_pending", table_name="wear_notifications")
    op.drop_table("wear_notifications")
