"""Add latest Alexa Discovery observations.

Revision ID: 20260820_0009
Revises: 20260819_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0009"
down_revision: str | None = "20260819_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alexa_discovery_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("endpoint_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("endpoints_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("changes_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["installation_id"], ["installations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("installation_id"),
    )
    op.create_index(
        "ix_alexa_discovery_snapshots_tenant",
        "alexa_discovery_snapshots",
        ["tenant_id"],
    )
    op.create_table(
        "alexa_discovery_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid()),
        sa.Column("alexa_endpoint_id", sa.String(255), nullable=False),
        sa.Column("representation_fingerprint", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["installation_id"], ["installations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["link_id"], ["alexa_account_links.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("link_id", "alexa_endpoint_id"),
    )
    op.create_index(
        "ix_alexa_discovery_deliveries_installation",
        "alexa_discovery_deliveries",
        ["installation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alexa_discovery_deliveries_installation",
        table_name="alexa_discovery_deliveries",
    )
    op.drop_table("alexa_discovery_deliveries")
    op.drop_index("ix_alexa_discovery_snapshots_tenant", table_name="alexa_discovery_snapshots")
    op.drop_table("alexa_discovery_snapshots")
