from sqlalchemy import select
import pytest

from apps.cloud_api.app.domain.models import Entity, Installation
from apps.cloud_api.app.alexa import alexa_entity_eligible
from apps.cloud_api.app.entity_sync import EntitySyncService, StaleSyncError
from apps.cloud_api.app.evcp import EntityItem, _entity_sync_payload
from apps.cloud_api.app.voice_scope import VOICE_SELECTION_KEY, voice_visible, voice_visible_clause

def item():
    return {'registry_id':'voice-scope-test', 'entity_id':'light.example', 'domain':'light',
            'friendly_name':'Example', 'available':True, 'state':'on', 'attributes':{}}


async def test_selection_snapshot_state_replay_and_legacy_sender(session, seeded_domain):
    installation = await session.get(Installation, seeded_domain.installation_a_id)
    sync = EntitySyncService(session, installation)
    selected = item(); selected['control_exposed'] = True
    await sync.apply_full(1, [selected])
    entity = (await session.scalars(select(Entity).where(Entity.ha_registry_id == selected['registry_id']))).one()
    assert entity.control_exposed is True and voice_visible(entity)
    deselected = item(); deselected['control_exposed'] = False
    await sync.apply_full(2, [deselected])
    assert entity.control_exposed is True and not voice_visible(entity)
    assert entity.attributes_json[VOICE_SELECTION_KEY] is False
    assert alexa_entity_eligible(entity), 'presentation scope must not revoke Alexa'
    wear_query = select(Entity.id).where(Entity.installation_id == installation.id,
        Entity.deleted_at.is_(None), Entity.available.is_(True),
        Entity.control_exposed.is_(True), Entity.ha_domain.in_(('light', 'switch')))
    assert entity.id in (await session.scalars(wear_query)).all(), 'Wear selector must remain unchanged'
    assert entity.id not in (await session.scalars(select(Entity.id).where(voice_visible_clause(Entity)))).all()
    with pytest.raises(StaleSyncError):
        await sync.apply_full(2, [selected])
    assert not voice_visible(entity)
    await sync.apply_state(3, [item()])
    assert not voice_visible(entity), 'legacy sender must not silently clear known selection'
    await sync.apply_state(4, [selected])
    assert voice_visible(entity) and entity.control_exposed is True
    assert entity.id in (await session.scalars(select(Entity.id).where(voice_visible_clause(Entity)))).all()


def test_missing_field_stays_missing_across_protocol():
    absent = EntityItem.model_validate(item())
    explicit = EntityItem.model_validate({**item(), 'control_exposed': False})
    assert 'control_exposed' not in _entity_sync_payload(absent)
    assert _entity_sync_payload(explicit)['control_exposed'] is False
