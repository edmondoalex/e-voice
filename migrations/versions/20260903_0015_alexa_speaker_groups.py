"""Add tenant-scoped Alexa speaker groups.

Revision ID: 20260903_0015
Revises: 20260902_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0015"
down_revision: str | None = "20260902_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alexa_speaker_groups",
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
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "installation_id", "slug", name="uq_alexa_speaker_groups_installation_slug"
        ),
    )
    op.create_index("ix_alexa_speaker_groups_tenant", "alexa_speaker_groups", ["tenant_id"])
    op.create_index(
        "ix_alexa_speaker_groups_installation", "alexa_speaker_groups", ["installation_id"]
    )
    op.create_table(
        "alexa_speaker_group_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("alexa_speaker_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.Uuid(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("group_id", "entity_id", name="uq_alexa_speaker_group_member"),
    )
    op.create_index(
        "ix_alexa_speaker_group_members_group", "alexa_speaker_group_members", ["group_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alexa_speaker_group_members_group", table_name="alexa_speaker_group_members"
    )
    op.drop_table("alexa_speaker_group_members")
    op.drop_index("ix_alexa_speaker_groups_installation", table_name="alexa_speaker_groups")
    op.drop_index("ix_alexa_speaker_groups_tenant", table_name="alexa_speaker_groups")
    op.drop_table("alexa_speaker_groups")
