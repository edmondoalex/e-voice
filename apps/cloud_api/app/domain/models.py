"""SQLAlchemy models for the multi-tenant core and secure pairing."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from .enums import InstallationStatus, PairingStatus, RecordStatus, TenantRole


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class Dealer(TimestampMixin, Base):
    __tablename__ = "dealers"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[RecordStatus] = mapped_column(
        Enum(
            RecordStatus,
            native_enum=False,
            length=20,
            values_callable=lambda values: [item.value for item in values],
        ),
        default=RecordStatus.ACTIVE,
    )

    tenants: Mapped[list["Tenant"]] = relationship(back_populates="dealer")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[RecordStatus] = mapped_column(
        Enum(
            RecordStatus,
            native_enum=False,
            length=20,
            values_callable=lambda values: [item.value for item in values],
        ),
        default=RecordStatus.ACTIVE,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["TenantMembership"]] = relationship(back_populates="user")


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    dealer_id: Mapped[UUID] = mapped_column(ForeignKey("dealers.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[RecordStatus] = mapped_column(
        Enum(
            RecordStatus,
            native_enum=False,
            length=20,
            values_callable=lambda values: [item.value for item in values],
        ),
        default=RecordStatus.ACTIVE,
    )

    dealer: Mapped[Dealer] = relationship(back_populates="tenants")
    memberships: Mapped[list["TenantMembership"]] = relationship(back_populates="tenant")
    installations: Mapped[list["Installation"]] = relationship(back_populates="tenant")


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[TenantRole] = mapped_column(
        Enum(
            TenantRole,
            native_enum=False,
            length=30,
            values_callable=lambda values: [item.value for item in values],
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class PortalSession(Base):
    __tablename__ = "portal_sessions"
    __table_args__ = (Index("ix_portal_sessions_expires", "expires_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    selected_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PortalLoginAttempt(Base):
    __tablename__ = "portal_login_attempts"
    __table_args__ = (Index("ix_portal_login_attempts_email_time", "email_hash", "attempted_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email_hash: Mapped[str] = mapped_column(String(64))
    successful: Mapped[bool] = mapped_column(Boolean, default=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Installation(TimestampMixin, Base):
    __tablename__ = "installations"
    __table_args__ = (Index("ix_installations_tenant_id", "tenant_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    public_id: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[InstallationStatus] = mapped_column(
        Enum(
            InstallationStatus,
            native_enum=False,
            length=20,
            values_callable=lambda values: [item.value for item in values],
        ),
        default=InstallationStatus.PENDING,
    )
    connector_version: Mapped[str | None] = mapped_column(String(50))
    ha_version: Mapped[str | None] = mapped_column(String(50))
    ha_installation_type: Mapped[str | None] = mapped_column(String(50))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    inventory_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped[Tenant] = relationship(back_populates="installations")
    entities: Mapped[list["Entity"]] = relationship(back_populates="installation")
    connector_credentials: Mapped[list["ConnectorCredential"]] = relationship(
        back_populates="installation", foreign_keys="ConnectorCredential.installation_id"
    )


class Entity(TimestampMixin, Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("installation_id", "ha_entity_id"),
        UniqueConstraint(
            "installation_id", "ha_registry_id", name="uq_entities_installation_registry"
        ),
        Index("ix_entities_installation_id", "installation_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("installations.id", ondelete="CASCADE")
    )
    ha_entity_id: Mapped[str] = mapped_column(String(255))
    ha_registry_id: Mapped[str | None] = mapped_column(String(64))
    ha_domain: Mapped[str] = mapped_column(String(64))
    icon: Mapped[str | None] = mapped_column(String(255))
    friendly_name: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(120))
    voice_name: Mapped[str | None] = mapped_column(String(120))
    voice_aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    area_id: Mapped[str | None] = mapped_column(String(255))
    area_name: Mapped[str | None] = mapped_column(String(255))
    device_id: Mapped[str | None] = mapped_column(String(64))
    device_name: Mapped[str | None] = mapped_column(String(255))
    device_class: Mapped[str | None] = mapped_column(String(100))
    supported_features: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[str | None] = mapped_column(String(255))
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    installation: Mapped[Installation] = relationship(back_populates="entities")
    alexa_publication: Mapped["AlexaPublication | None"] = relationship(
        back_populates="entity", uselist=False
    )


class EntityStateHistory(Base):
    __tablename__ = "entity_state_history"
    __table_args__ = (
        Index("ix_entity_state_history_installation_time", "installation_id", "recorded_at"),
        Index("ix_entity_state_history_entity_time", "entity_id", "recorded_at"),
        Index("ix_entity_state_history_tenant_time", "tenant_id", "recorded_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("installations.id", ondelete="CASCADE")
    )
    entity_id: Mapped[UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    state: Mapped[str | None] = mapped_column(String(255))
    available: Mapped[bool] = mapped_column(Boolean)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class OperationalEvent(Base):
    __tablename__ = "operational_events"
    __table_args__ = (
        Index("ix_operational_events_installation_time", "installation_id", "created_at"),
        Index("ix_operational_events_tenant_time", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    installation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("installations.id", ondelete="CASCADE")
    )
    entity_id: Mapped[UUID | None] = mapped_column(ForeignKey("entities.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(50))
    outcome: Mapped[str] = mapped_column(String(50))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class MaintenanceRun(Base):
    __tablename__ = "maintenance_runs"
    __table_args__ = (Index("ix_maintenance_runs_kind_started", "kind", "started_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    deleted_counts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))


class AlexaPublication(TimestampMixin, Base):
    __tablename__ = "alexa_publications"
    __table_args__ = (Index("ix_alexa_publications_endpoint_id", "alexa_endpoint_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    display_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    display_category: Mapped[str | None] = mapped_column(String(100))
    mapper_type: Mapped[str] = mapped_column(String(100))
    control_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    state_read_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    alexa_endpoint_id: Mapped[str] = mapped_column(String(255), unique=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    entity: Mapped[Entity] = relationship(back_populates="alexa_publication")


class AlexaAccountLink(TimestampMixin, Base):
    __tablename__ = "alexa_account_links"
    __table_args__ = (Index("ix_alexa_links_tenant", "tenant_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider_subject: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    unlinked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlexaOAuthGrant(Base):
    __tablename__ = "alexa_oauth_grants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    link_id: Mapped[UUID] = mapped_column(ForeignKey("alexa_account_links.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    redirect_uri: Mapped[str] = mapped_column(String(1000))
    code_challenge: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AlexaOAuthToken(Base):
    __tablename__ = "alexa_oauth_tokens"
    __table_args__ = (Index("ix_alexa_tokens_link", "link_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    link_id: Mapped[UUID] = mapped_column(ForeignKey("alexa_account_links.id", ondelete="CASCADE"))
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlexaEventAuthorization(TimestampMixin, Base):
    __tablename__ = "alexa_event_authorizations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    link_id: Mapped[UUID] = mapped_column(
        ForeignKey("alexa_account_links.id", ondelete="CASCADE"), unique=True
    )
    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    refresh_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlexaReportedState(Base):
    __tablename__ = "alexa_reported_states"
    __table_args__ = (UniqueConstraint("link_id", "entity_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    link_id: Mapped[UUID] = mapped_column(ForeignKey("alexa_account_links.id", ondelete="CASCADE"))
    entity_id: Mapped[UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    property_fingerprint: Mapped[str] = mapped_column(String(64))
    properties_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AlexaDiscoverySnapshot(TimestampMixin, Base):
    __tablename__ = "alexa_discovery_snapshots"
    __table_args__ = (
        Index("ix_alexa_discovery_snapshots_tenant", "tenant_id"),
        UniqueConstraint("installation_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("installations.id", ondelete="CASCADE")
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    endpoint_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    endpoints_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    changes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class AlexaDiscoveryDelivery(Base):
    """Per-account delivery ledger; authoritative endpoint data remains on Entity."""

    __tablename__ = "alexa_discovery_deliveries"
    __table_args__ = (
        UniqueConstraint("link_id", "alexa_endpoint_id"),
        Index("ix_alexa_discovery_deliveries_installation", "installation_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    link_id: Mapped[UUID] = mapped_column(ForeignKey("alexa_account_links.id", ondelete="CASCADE"))
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("installations.id", ondelete="CASCADE")
    )
    entity_id: Mapped[UUID | None] = mapped_column(ForeignKey("entities.id", ondelete="SET NULL"))
    alexa_endpoint_id: Mapped[str] = mapped_column(String(255))
    representation_fingerprint: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    installation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("installations.id", ondelete="SET NULL")
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(100))
    request_id: Mapped[str | None] = mapped_column(String(100))
    payload_redacted_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class ConnectorCredential(Base):
    __tablename__ = "connector_credentials"
    __table_args__ = (Index("ix_connector_credentials_installation", "installation_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("installations.id", ondelete="CASCADE")
    )
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    rotated_from_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connector_credentials.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    installation: Mapped[Installation] = relationship(
        back_populates="connector_credentials", foreign_keys=[installation_id]
    )


class PairingSession(Base):
    __tablename__ = "pairing_sessions"
    __table_args__ = (
        Index("ix_pairing_sessions_expires_at", "expires_at"),
        Index("ix_pairing_sessions_nonce_status", "installation_nonce", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    polling_secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    installation_nonce: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[PairingStatus] = mapped_column(
        Enum(
            PairingStatus,
            native_enum=False,
            length=20,
            values_callable=lambda values: [item.value for item in values],
        ),
        default=PairingStatus.PENDING,
    )
    claimed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    claimed_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL")
    )
    claimed_installation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("installations.id", ondelete="SET NULL")
    )
    connector_credential_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connector_credentials.id", ondelete="SET NULL")
    )
    credential_envelope: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credential_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PairingClaimAttempt(Base):
    __tablename__ = "pairing_claim_attempts"
    __table_args__ = (Index("ix_pairing_claim_attempts_user_time", "user_id", "attempted_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    pairing_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pairing_sessions.id", ondelete="SET NULL")
    )
    successful: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[str] = mapped_column(String(50))
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
