"""Add revocable user-to-installation assignments.

Revision ID: 20260928_0023
Revises: 20260911_0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0023"
down_revision: str | None = "20260911_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("installations") as batch:
        batch.create_unique_constraint("uq_installations_id_tenant", ["id", "tenant_id"])
    op.create_table(
        "user_installation_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("access_level", sa.String(20), server_default="use", nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "tenant_id"],
            ["installations.id", "installations.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "installation_id", name="uq_user_installation_assignment"
        ),
    )
    op.create_index(
        "ix_user_installation_assignments_user",
        "user_installation_assignments",
        ["tenant_id", "user_id"],
    )
    op.create_index(
        "ix_user_installation_assignments_installation",
        "user_installation_assignments",
        ["installation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_installation_assignments_installation",
        table_name="user_installation_assignments",
    )
    op.drop_index(
        "ix_user_installation_assignments_user",
        table_name="user_installation_assignments",
    )
    op.drop_table("user_installation_assignments")
    with op.batch_alter_table("installations") as batch:
        batch.drop_constraint("uq_installations_id_tenant", type_="unique")
