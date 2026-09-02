"""Add tenant voice categories and entity assignments.

Revision ID: 20260902_0014
Revises: 20260825_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0014"
down_revision: str | None = "20260825_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "voice_categories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_voice_categories_tenant_slug"),
    )
    op.create_index("ix_voice_categories_tenant_id", "voice_categories", ["tenant_id"])
    with op.batch_alter_table("entities") as batch:
        batch.add_column(sa.Column("voice_category_id", sa.Uuid()))
        batch.create_foreign_key(
            "fk_entities_voice_category_id",
            "voice_categories",
            ["voice_category_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("entities") as batch:
        batch.drop_constraint("fk_entities_voice_category_id", type_="foreignkey")
        batch.drop_column("voice_category_id")
    op.drop_index("ix_voice_categories_tenant_id", table_name="voice_categories")
    op.drop_table("voice_categories")
