"""Staging-only durable selection snapshots, separate from inventory/Alexa."""
from uuid import UUID
from datetime import UTC, datetime, timedelta
from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, String, Uuid, delete, select
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
from .domain.models import ConnectorCredential, Installation

class VoiceSelectionSnapshot(Base):
    __tablename__='voice_selection_snapshot'
    installation_id: Mapped[UUID]=mapped_column(Uuid,ForeignKey('installations.id'),primary_key=True)
    tenant_id: Mapped[UUID]=mapped_column(Uuid,ForeignKey('tenants.id'),nullable=False)
    revision: Mapped[int]=mapped_column(BigInteger,nullable=False)
    registry_ids: Mapped[list]=mapped_column(JSON,nullable=False)
    session_id: Mapped[UUID]=mapped_column(Uuid,nullable=False)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)

class VoiceSelectionFence(Base):
    __tablename__='voice_selection_session_fence'
    installation_id: Mapped[UUID]=mapped_column(Uuid,ForeignKey('installations.id'),primary_key=True)
    tenant_id: Mapped[UUID]=mapped_column(Uuid,ForeignKey('tenants.id'),nullable=False)
    session_id: Mapped[UUID]=mapped_column(Uuid,nullable=False)
    credential_id: Mapped[UUID | None]=mapped_column(Uuid,ForeignKey('connector_credentials.id'),nullable=True)
    active: Mapped[bool]=mapped_column(Boolean,nullable=False,default=True)

class VoiceSelectionMember(Base):
    __tablename__='voice_selection_member'
    installation_id: Mapped[UUID]=mapped_column(Uuid,ForeignKey('installations.id'),primary_key=True)
    registry_id: Mapped[str]=mapped_column(String(64),primary_key=True)

async def register_selection_session(session,installation_id,tenant_id,session_id,credential_id=None):
    instance=await session.scalar(select(Installation).where(Installation.id==installation_id).with_for_update().execution_options(populate_existing=True))
    if instance is None or instance.tenant_id!=tenant_id:raise ValueError('selection_scope_invalid')
    fence=await session.get(VoiceSelectionFence,installation_id,populate_existing=True)
    if fence is None:session.add(VoiceSelectionFence(installation_id=installation_id,tenant_id=tenant_id,session_id=session_id,credential_id=credential_id,active=True))
    elif fence.tenant_id!=tenant_id:raise ValueError('selection_scope_invalid')
    else:fence.session_id=session_id;fence.credential_id=credential_id;fence.active=True
    await session.flush()

async def end_selection_session(session,installation_id,session_id):
    await session.scalar(select(Installation).where(Installation.id==installation_id).with_for_update())
    fence=await session.get(VoiceSelectionFence,installation_id,populate_existing=True)
    if fence is not None and fence.session_id==session_id:fence.active=False
    await session.flush()

async def persist_selection(session, installation_id, tenant_id, session_id, revision, ids, *, require_inventory_revision=False):
    instance=await session.scalar(select(Installation).where(Installation.id==installation_id).with_for_update().execution_options(populate_existing=True))
    if instance is None or instance.tenant_id!=tenant_id:raise ValueError('selection_scope_invalid')
    if require_inventory_revision and (type(revision) is not int or revision!=instance.sync_revision):raise ValueError('selection_inventory_revision_mismatch')
    fence=await session.get(VoiceSelectionFence,installation_id,populate_existing=True)
    if fence is None or not fence.active or fence.tenant_id!=tenant_id or fence.session_id!=session_id:raise ValueError('selection_session_replaced')
    if fence.credential_id is not None:
        credential=await session.scalar(select(ConnectorCredential).where(ConnectorCredential.id==fence.credential_id).execution_options(populate_existing=True))
        if credential is None or credential.installation_id!=installation_id or credential.revoked_at is not None:raise ValueError('selection_credential_revoked')
    if len(ids)!=len(set(ids)):raise ValueError('selection_duplicate')
    row=await session.scalar(select(VoiceSelectionSnapshot).where(VoiceSelectionSnapshot.installation_id==installation_id).with_for_update().execution_options(populate_existing=True))
    ordered=sorted(ids)
    if row is None:
        row=VoiceSelectionSnapshot(installation_id=installation_id,tenant_id=tenant_id,revision=revision,registry_ids=ordered,session_id=session_id,expires_at=datetime.now(UTC)+timedelta(minutes=5));session.add(row)
    elif row.tenant_id!=tenant_id or revision<row.revision or (revision==row.revision and ordered!=row.registry_ids):
        raise ValueError('selection_stale_or_conflict')
    elif revision>row.revision:
        row.revision=revision;row.registry_ids=ordered
    row.session_id=session_id;row.expires_at=datetime.now(UTC)+timedelta(minutes=5)
    await session.execute(delete(VoiceSelectionMember).where(VoiceSelectionMember.installation_id==installation_id))
    session.add_all([VoiceSelectionMember(installation_id=installation_id,registry_id=identity) for identity in ordered])
    await session.flush()
