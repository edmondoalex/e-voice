"""Wear FCM token per revocable device."""

import sqlalchemy as sa
from alembic import op

revision = "20261118_0029"
down_revision = "20261116_0028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("wear_devices", sa.Column("fcm_token_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "wear_devices",
        sa.Column("fcm_token_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("wear_devices", "fcm_token_updated_at")
    op.drop_column("wear_devices", "fcm_token_encrypted")
