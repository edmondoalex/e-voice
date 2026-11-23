"""Staged CHANGE083 bridge tables; does not activate or transfer authority."""

import sqlalchemy as sa
from alembic import op

revision = "20261120_0031"
down_revision = "20261118_0030"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "wear_bridge_authority",
        sa.Column(
            "installation_id", sa.Uuid(), sa.ForeignKey("installations.id"), primary_key=True
        ),
        sa.Column("owner", sa.String(16), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("import_revision", sa.String(64), nullable=True),
    )
    op.create_table(
        "wear_bridge_nonce",
        sa.Column(
            "installation_id", sa.Uuid(), sa.ForeignKey("installations.id"), primary_key=True
        ),
        sa.Column("nonce", sa.String(128), primary_key=True),
        sa.Column("expires", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "wear_bridge_inbox",
        sa.Column(
            "installation_id", sa.Uuid(), sa.ForeignKey("installations.id"), primary_key=True
        ),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("body_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "wear_bridge_wake",
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("wear_devices.id"), primary_key=True),
        sa.Column("event_key", sa.String(120), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )


def downgrade():
    for table in (
        "wear_bridge_wake",
        "wear_bridge_inbox",
        "wear_bridge_nonce",
        "wear_bridge_authority",
    ):
        op.drop_table(table)
