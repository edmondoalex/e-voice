"""Create the M1 core multi-tenant schema.

Revision ID: 20260817_0001
Revises:
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

record_status = sa.Enum("active", "disabled", name="recordstatus", native_enum=False, length=20)
installation_status = sa.Enum(
    "pending",
    "active",
    "revoked",
    name="installationstatus",
    native_enum=False,
    length=20,
)
tenant_role = sa.Enum(
    "owner",
    "dealer_admin",
    "installer",
    "customer_admin",
    "customer_user",
    "support_readonly",
    name="tenantrole",
    native_enum=False,
    length=30,
)


def timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def upgrade() -> None:
    op.create_table(
        "dealers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("status", record_status, nullable=False),
        *timestamps(),
        sa.UniqueConstraint("slug", name="uq_dealers_slug"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", record_status, nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "dealer_id", sa.Uuid(), sa.ForeignKey("dealers.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("status", record_status, nullable=False),
        *timestamps(),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("role", tenant_role, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_tenant_id"),
    )
    op.create_table(
        "installations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("public_id", sa.String(100), nullable=False),
        sa.Column("status", installation_status, nullable=False),
        sa.Column("connector_version", sa.String(50)),
        sa.Column("ha_version", sa.String(50)),
        sa.Column("ha_installation_type", sa.String(50)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("public_id", name="uq_installations_public_id"),
    )
    op.create_index("ix_installations_tenant_id", "installations", ["tenant_id"])
    op.create_table(
        "entities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "installation_id",
            sa.Uuid(),
            sa.ForeignKey("installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ha_entity_id", sa.String(255), nullable=False),
        sa.Column("ha_domain", sa.String(64), nullable=False),
        sa.Column("friendly_name", sa.String(255)),
        sa.Column("area_id", sa.String(255)),
        sa.Column("area_name", sa.String(255)),
        sa.Column("device_class", sa.String(100)),
        sa.Column("supported_features", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(255)),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("installation_id", "ha_entity_id", name="uq_entities_installation_id"),
    )
    op.create_index("ix_entities_installation_id", "entities", ["installation_id"])
    op.create_table(
        "alexa_publications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "entity_id", sa.Uuid(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("display_category", sa.String(100)),
        sa.Column("mapper_type", sa.String(100), nullable=False),
        sa.Column("control_allowed", sa.Boolean(), nullable=False),
        sa.Column("state_read_allowed", sa.Boolean(), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("alexa_endpoint_id", sa.String(255), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("entity_id", name="uq_alexa_publications_entity_id"),
        sa.UniqueConstraint("alexa_endpoint_id", name="uq_alexa_publications_alexa_endpoint_id"),
    )
    op.create_index(
        "ix_alexa_publications_endpoint_id", "alexa_publications", ["alexa_endpoint_id"]
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "installation_id",
            sa.Uuid(),
            sa.ForeignKey("installations.id", ondelete="SET NULL"),
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("request_id", sa.String(100)),
        sa.Column("payload_redacted_json", sa.JSON(), nullable=False),
        sa.Column("result", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_audit_events_tenant_created", "audit_events", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("alexa_publications")
    op.drop_table("entities")
    op.drop_table("installations")
    op.drop_table("tenant_memberships")
    op.drop_table("tenants")
    op.drop_table("users")
    op.drop_table("dealers")
