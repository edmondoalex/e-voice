from datetime import UTC,datetime,timedelta
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import select
import pytest
from apps.cloud_api.app import voice_scope
from apps.cloud_api.app.alexa import alexa_entity_eligible
from apps.cloud_api.app.domain.models import ConnectorCredential,Entity
from apps.cloud_api.app.voice_selection_store import (VoiceSelectionSnapshot,register_selection_session,
    persist_selection,end_selection_session)

async def test_projection_preserves_alexa_and_hides_on_expiry_replacement_and_revoke(session,seeded_domain,monkeypatch):
    installation=seeded_domain.installation_a_id;tenant=seeded_domain.tenant_a_id;sid=uuid4()
    first=await session.get(Entity,seeded_domain.entity_a_id);first.ha_registry_id='selected'
    second=Entity(installation_id=installation,ha_registry_id='unselected',ha_entity_id='switch.other',ha_domain='switch',control_exposed=True)
    credential=ConnectorCredential(installation_id=installation,secret_hash='a'*64)
    session.add_all([second,credential]);await session.flush();first_id,second_id,credential_id=first.id,second.id,credential.id
    await register_selection_session(session,installation,tenant,sid,credential.id)
    await persist_selection(session,installation,tenant,sid,1,['selected','selected-no-state']);await session.commit()
    async def visible():
        return set((await session.scalars(select(Entity.id).where(Entity.installation_id==installation,voice_scope.voice_visible_clause(Entity)))).all())
    settings=SimpleNamespace(voice_selection_v1_enabled=False,voice_selection_projection_enabled=False)
    monkeypatch.setattr(voice_scope,'get_settings',lambda:settings)
    assert await visible()=={first_id,second_id}
    settings.voice_selection_v1_enabled=True;settings.voice_selection_projection_enabled=True
    assert await visible()=={first_id}
    assert first.control_exposed and second.control_exposed and alexa_entity_eligible(first)
    snapshot=await session.get(VoiceSelectionSnapshot,installation)
    snapshot.expires_at=datetime.now(UTC)-timedelta(seconds=1);await session.commit()
    assert await visible()==set()
    await persist_selection(session,installation,tenant,sid,1,['selected','selected-no-state']);await session.commit()
    assert await visible()=={first_id}
    replacement=uuid4();await register_selection_session(session,installation,tenant,replacement,credential.id);await session.commit()
    assert await visible()==set()
    await persist_selection(session,installation,tenant,replacement,2,['selected']);await session.commit()
    assert await visible()=={first_id}
    credential.revoked_at=datetime.now(UTC);await session.commit()
    assert await visible()==set()
    with pytest.raises(ValueError,match='credential_revoked'):
        await persist_selection(session,installation,tenant,replacement,3,['selected'])
    await session.rollback()
    credential=await session.get(ConnectorCredential,credential_id)
    credential.revoked_at=None;await session.commit()
    await end_selection_session(session,installation,replacement);await session.commit()
    assert await visible()==set()
