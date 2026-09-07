"""Add one-shot Alexa voice alerts.

Revision ID: 20260907_0017
Revises: 20260907_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0017"
down_revision: str | None = "20260907_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alexa_voice_alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "installation_id",
            sa.Uuid(),
            sa.ForeignKey("installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_entity_id",
            sa.Uuid(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expected_state", sa.String(64), nullable=False),
        sa.Column("source_device_id", sa.String(64), nullable=True),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_alexa_voice_alerts_installation_status",
        "alexa_voice_alerts",
        ["installation_id", "status"],
    )
    op.create_index("ix_alexa_voice_alerts_target", "alexa_voice_alerts", ["target_entity_id"])


def downgrade() -> None:
    op.drop_index("ix_alexa_voice_alerts_target", table_name="alexa_voice_alerts")
    op.drop_index("ix_alexa_voice_alerts_installation_status", table_name="alexa_voice_alerts")
    op.drop_table("alexa_voice_alerts")
