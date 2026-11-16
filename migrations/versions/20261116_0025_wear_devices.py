# ruff: noqa: E501
"""Add official per-device Wear enrollment and identity.

Revision ID: 20261116_0025
Revises: 20261115_0024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261116_0025"
down_revision: str | None = "20261115_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wear_enrollments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_wear_enrollments_expires", "wear_enrollments", ["expires_at"])
    op.create_table(
        "wear_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("credential_hash", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_hash"),
    )
    op.create_index("ix_wear_devices_owner", "wear_devices", ["tenant_id", "user_id"])
    op.create_index("ix_wear_devices_installation", "wear_devices", ["installation_id"])


def downgrade() -> None:
    op.drop_index("ix_wear_devices_installation", table_name="wear_devices")
    op.drop_index("ix_wear_devices_owner", table_name="wear_devices")
    op.drop_table("wear_devices")
    op.drop_index("ix_wear_enrollments_expires", table_name="wear_enrollments")
    op.drop_table("wear_enrollments")
