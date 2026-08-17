"""Add secure pairing and connector credentials.

Revision ID: 20260817_0002
Revises: 20260817_0001
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0002"
down_revision: str | None = "20260817_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

pairing_status = sa.Enum(
    "pending",
    "claimed",
    "expired",
    "locked",
    name="pairingstatus",
    native_enum=False,
    length=20,
)


def upgrade() -> None:
    op.create_table(
        "connector_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "installation_id",
            sa.Uuid(),
            sa.ForeignKey("installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column(
            "rotated_from_id",
            sa.Uuid(),
            sa.ForeignKey("connector_credentials.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("secret_hash", name="uq_connector_credentials_secret_hash"),
    )
    op.create_index(
        "ix_connector_credentials_installation", "connector_credentials", ["installation_id"]
    )
    op.create_table(
        "pairing_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("polling_secret_hash", sa.String(64), nullable=False),
        sa.Column("installation_nonce", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", pairing_status, nullable=False),
        sa.Column(
            "claimed_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "claimed_tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "claimed_installation_id",
            sa.Uuid(),
            sa.ForeignKey("installations.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "connector_credential_id",
            sa.Uuid(),
            sa.ForeignKey("connector_credentials.id", ondelete="SET NULL"),
        ),
        sa.Column("credential_envelope", sa.LargeBinary()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("credential_delivered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("code_hash", name="uq_pairing_sessions_code_hash"),
        sa.UniqueConstraint("polling_secret_hash", name="uq_pairing_sessions_polling_secret_hash"),
    )
    op.create_index("ix_pairing_sessions_expires_at", "pairing_sessions", ["expires_at"])
    op.create_index(
        "ix_pairing_sessions_nonce_status",
        "pairing_sessions",
        ["installation_nonce", "status"],
    )
    op.create_table(
        "pairing_claim_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "pairing_session_id",
            sa.Uuid(),
            sa.ForeignKey("pairing_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column("successful", sa.Boolean(), nullable=False),
        sa.Column("result", sa.String(50), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_pairing_claim_attempts_user_time",
        "pairing_claim_attempts",
        ["user_id", "attempted_at"],
    )


def downgrade() -> None:
    op.drop_table("pairing_claim_attempts")
    op.drop_table("pairing_sessions")
    op.drop_table("connector_credentials")
