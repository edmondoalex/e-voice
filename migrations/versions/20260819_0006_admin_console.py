"""Add bounded state history and operational events.

Revision ID: 20260819_0006
Revises: 20260819_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_0006"
down_revision: str | None = "20260819_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_state_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "installation_id",
            sa.Uuid(),
            sa.ForeignKey("installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id", sa.Uuid(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("state", sa.String(255)),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_entity_state_history_installation_time",
        "entity_state_history",
        ["installation_id", "recorded_at"],
    )
    op.create_index(
        "ix_entity_state_history_entity_time", "entity_state_history", ["entity_id", "recorded_at"]
    )
    op.create_index(
        "ix_entity_state_history_tenant_time", "entity_state_history", ["tenant_id", "recorded_at"]
    )
    op.create_table(
        "operational_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "installation_id", sa.Uuid(), sa.ForeignKey("installations.id", ondelete="CASCADE")
        ),
        sa.Column("entity_id", sa.Uuid(), sa.ForeignKey("entities.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("outcome", sa.String(50), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_operational_events_installation_time",
        "operational_events",
        ["installation_id", "created_at"],
    )
    op.create_index(
        "ix_operational_events_tenant_time", "operational_events", ["tenant_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("operational_events")
    op.drop_table("entity_state_history")
