"""Add CHANGE028 external bindings and bounded sessions.

Revision ID: 20261115_0024
Revises: 20260928_0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261115_0024"
down_revision: str | None = "20260928_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_installation_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(200), nullable=False),
        sa.Column("external_installation_id", sa.String(200), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["installation_id"], ["installations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "external_installation_id", name="uq_external_binding"),
        sa.UniqueConstraint("installation_id", name="uq_external_binding_installation"),
    )
    op.create_table(
        "installation_bound_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("launch_jti_hash", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(200), nullable=False),
        sa.Column("issuer", sa.String(200), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("effective_installation_id", sa.Uuid(), nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["effective_installation_id"], ["installations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.UniqueConstraint("launch_jti_hash"),
    )
    op.create_index("ix_bound_sessions_expires", "installation_bound_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_bound_sessions_expires", table_name="installation_bound_sessions")
    op.drop_table("installation_bound_sessions")
    op.drop_table("external_installation_bindings")
