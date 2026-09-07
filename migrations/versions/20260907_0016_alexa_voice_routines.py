"""Add portal-defined Alexa voice routines.

Revision ID: 20260907_0016
Revises: 20260903_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0016"
down_revision: str | None = "20260903_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alexa_voice_routines",
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
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="announce"),
        sa.Column("destination_type", sa.String(16), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=True),
        sa.Column("default_message", sa.String(500), nullable=True),
        sa.Column("volume_percent", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "installation_id", "slug", name="uq_alexa_voice_routines_installation_slug"
        ),
    )
    op.create_index("ix_alexa_voice_routines_tenant", "alexa_voice_routines", ["tenant_id"])
    op.create_index(
        "ix_alexa_voice_routines_installation", "alexa_voice_routines", ["installation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_alexa_voice_routines_installation", table_name="alexa_voice_routines")
    op.drop_index("ix_alexa_voice_routines_tenant", table_name="alexa_voice_routines")
    op.drop_table("alexa_voice_routines")
