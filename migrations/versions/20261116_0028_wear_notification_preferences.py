"""Add per-device Wear notification preferences.

Revision ID: 20261116_0028
Revises: 20261116_0027
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261116_0028"
down_revision: str | None = "20261116_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DEFAULT = json.dumps(
    {
        "enabled": True,
        "categories": [
            "security",
            "climate",
            "energy",
            "routines",
            "maintenance",
            "system",
        ],
        "minimum_severity": "info",
        "vibration": True,
        "quiet_start": None,
        "quiet_end": None,
        "timezone": "Europe/Rome",
    },
    separators=(",", ":"),
)


def upgrade() -> None:
    op.add_column(
        "wear_devices",
        sa.Column(
            "notification_preferences_json",
            sa.JSON(),
            nullable=False,
            server_default=DEFAULT,
        ),
    )


def downgrade() -> None:
    op.drop_column("wear_devices", "notification_preferences_json")
