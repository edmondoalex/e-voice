"""Add advanced Alexa routine options and execution history.

Revision ID: 20260907_0018
Revises: 20260907_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0018"
down_revision: str | None = "20260907_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        sa.Column("night_volume_percent", sa.Integer(), nullable=True),
        sa.Column("night_start", sa.String(5), nullable=True),
        sa.Column("night_end", sa.String(5), nullable=True),
        sa.Column("restore_volume", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sound", sa.String(20), nullable=False, server_default="default"),
        sa.Column("repeat_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("repeat_interval_seconds", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("condition_entity_id", sa.Uuid(), nullable=True),
        sa.Column("condition_state", sa.String(64), nullable=True),
        sa.Column("condition_start", sa.String(5), nullable=True),
        sa.Column("condition_end", sa.String(5), nullable=True),
    ):
        op.add_column("alexa_voice_routines", column)
    op.create_foreign_key(
        "fk_alexa_voice_routines_condition_entity",
        "alexa_voice_routines",
        "entities",
        ["condition_entity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "alexa_routine_executions",
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
            "routine_id",
            sa.Uuid(),
            sa.ForeignKey("alexa_voice_routines.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("destination", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("volume_percent", sa.Integer(), nullable=True),
        sa.Column("message_preview", sa.String(120), nullable=False),
        sa.Column("attempted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("detail", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_alexa_routine_executions_tenant_created",
        "alexa_routine_executions",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_alexa_routine_executions_routine", "alexa_routine_executions", ["routine_id"]
    )


def downgrade() -> None:
    op.drop_table("alexa_routine_executions")
    op.drop_constraint(
        "fk_alexa_voice_routines_condition_entity", "alexa_voice_routines", type_="foreignkey"
    )
    for name in (
        "condition_end",
        "condition_start",
        "condition_state",
        "condition_entity_id",
        "priority",
        "repeat_interval_seconds",
        "repeat_count",
        "sound",
        "restore_volume",
        "night_end",
        "night_start",
        "night_volume_percent",
    ):
        op.drop_column("alexa_voice_routines", name)
