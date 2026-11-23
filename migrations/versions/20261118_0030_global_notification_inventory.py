"""Separate global notification visibility from control exposure."""

import sqlalchemy as sa
from alembic import op

revision = "20261118_0030"
down_revision = "20261118_0029"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "entities",
        sa.Column("control_exposed", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade():
    op.drop_column("entities", "control_exposed")
