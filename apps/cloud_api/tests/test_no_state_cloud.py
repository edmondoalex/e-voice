from unittest.mock import AsyncMock
from sqlalchemy import select
from apps.cloud_api.app.entity_sync import EntitySyncService
from apps.cloud_api.app.domain.models import Entity, Installation, AlexaPublication, VoiceCategory
from apps.cloud_api.app.command_dispatch import CommandDispatchService,PowerCommand
from apps.cloud_api.app.evcp import EntityItem

async def test_existing_placeholder_return_preserves_customization_and_blocks_commands(session,seeded_domain):
    inst=await session.get(Installation,seeded_domain.installation_a_id)
    entity=await session.get(Entity,seeded_domain.entity_a_id)
    entity.ha_registry_id='existing-guard';entity.voice_name='Nome personalizzato';entity.display_name='Nome schermo';entity.voice_aliases=['Alias'];entity.control_exposed=True;entity.available=True
    category=VoiceCategory(tenant_id=seeded_domain.tenant_a_id,slug='guard-category',name='Guard category',builtin=False);session.add(category);await session.flush();entity.voice_category_id=category.id
    publication=await session.scalar(select(AlexaPublication).where(AlexaPublication.entity_id==entity.id));assert publication is not None
    initial=(publication.id,publication.enabled,publication.mapper_type,publication.control_allowed,publication.state_read_allowed)
    eid=entity.id;cid=category.id;await session.commit()
    placeholder={'registry_only':True,'registry_id':'existing-guard','entity_id':entity.ha_entity_id,'domain':entity.ha_domain,'friendly_name':'Fallback','available':False,'control_exposed':False,'removed':False}
    EntityItem.model_validate(placeholder)
    service=EntitySyncService(session,inst,registry_only_negotiated=True);await service.apply_full(inst.sync_revision+1,[placeholder]);await session.commit()
    await session.refresh(entity);await session.refresh(publication)
    assert (entity.id,entity.voice_name,entity.display_name,entity.voice_aliases,entity.voice_category_id)==(eid,'Nome personalizzato','Nome schermo',['Alias'],cid)
    assert entity.available is False and (publication.id,publication.enabled,publication.mapper_type,publication.control_allowed,publication.state_read_allowed)==initial
    router=AsyncMock();out=await CommandDispatchService(session,router).dispatch(inst.id,'existing-guard',PowerCommand(operation='power_on'))
    assert out.status=='unavailable';router.dispatch.assert_not_awaited()
    restored={**placeholder,'registry_only':False,'available':True,'control_exposed':True,'state':'off'}
    await service.apply_full(inst.sync_revision+1,[restored]);await session.commit();await session.refresh(entity);await session.refresh(publication)
    assert entity.available is True and (entity.id,entity.voice_name,entity.display_name,entity.voice_aliases,entity.voice_category_id)==(eid,'Nome personalizzato','Nome schermo',['Alias'],cid)
    assert (publication.id,publication.enabled,publication.mapper_type,publication.control_allowed,publication.state_read_allowed)==initial

async def test_new_placeholder_is_visible_under_strict_selection_but_never_commanded(session,seeded_domain,monkeypatch):
    from uuid import uuid4
    from types import SimpleNamespace
    from apps.cloud_api.app import voice_scope
    from apps.cloud_api.app.domain.models import ConnectorCredential
    from apps.cloud_api.app.voice_selection_store import register_selection_session,persist_selection
    inst=await session.get(Installation,seeded_domain.installation_a_id); sid=uuid4()
    credential=ConnectorCredential(installation_id=inst.id,secret_hash='d'*64);session.add(credential);await session.flush()
    await register_selection_session(session,inst.id,seeded_domain.tenant_a_id,sid,credential.id)
    placeholder={'registry_only':True,'registry_id':'no-state-guard','entity_id':'sensor.no_state_guard','domain':'sensor','friendly_name':'Guard','available':False,'control_exposed':False,'removed':False}
    EntityItem.model_validate(placeholder)
    await EntitySyncService(session,inst,registry_only_negotiated=True).apply_full(inst.sync_revision+1,[placeholder])
    await persist_selection(session,inst.id,seeded_domain.tenant_a_id,sid,1,['no-state-guard']);await session.commit()
    monkeypatch.setattr(voice_scope,'get_settings',lambda:SimpleNamespace(voice_selection_v1_enabled=True,voice_selection_projection_enabled=True))
    entity=await session.scalar(select(Entity).where(Entity.installation_id==inst.id,Entity.ha_registry_id=='no-state-guard',voice_scope.voice_visible_clause(Entity)))
    assert entity is not None and entity.available is False and entity.control_exposed is False
    router=AsyncMock();out=await CommandDispatchService(session,router).dispatch(inst.id,'no-state-guard',PowerCommand(operation='power_on'))
    assert out.status=='unavailable';router.dispatch.assert_not_awaited()

async def test_existing_placeholder_preserves_media_capabilities(session,seeded_domain):
    from apps.cloud_api.app.alexa import alexa_entity_eligible, capabilities
    inst=await session.get(Installation,seeded_domain.installation_a_id);entity=await session.get(Entity,seeded_domain.entity_a_id)
    entity.ha_registry_id='media-no-state';entity.ha_entity_id='media_player.synthetic';entity.ha_domain='media_player';entity.supported_features=128;entity.device_class='speaker';entity.attributes_json={'source_list':['radio']};entity.control_exposed=True
    before=(alexa_entity_eligible(entity),capabilities(entity))
    await session.commit()
    placeholder={'registry_only':True,'registry_id':'media-no-state','entity_id':'media_player.synthetic','domain':'media_player','friendly_name':'Fallback','available':False,'control_exposed':False,'removed':False}
    await EntitySyncService(session,inst,registry_only_negotiated=True).apply_full(inst.sync_revision+1,[placeholder]);await session.commit();await session.refresh(entity)
    assert entity.supported_features==128 and entity.device_class=='speaker' and entity.attributes_json.get('source_list')==['radio']
    assert (alexa_entity_eligible(entity),capabilities(entity))==before
    restored={**placeholder,'registry_only':False,'available':True,'control_exposed':True,'supported_features':128,'device_class':'speaker','attributes':{'source_list':['radio']},'state':'off'}
    await EntitySyncService(session,inst,registry_only_negotiated=True).apply_full(inst.sync_revision+1,[restored]);await session.commit();await session.refresh(entity)
    assert (alexa_entity_eligible(entity),capabilities(entity))==before

async def test_registry_only_requires_negotiation_and_invalid_flag_fails_closed(session,seeded_domain):
    import pytest
    inst=await session.get(Installation,seeded_domain.installation_a_id)
    payload={'registry_only':True,'registry_id':'invalid-placeholder','entity_id':'sensor.invalid_placeholder','domain':'sensor','available':False,'control_exposed':False}
    with pytest.raises(ValueError,match='registry_only_not_negotiated'):
        await EntitySyncService(session,inst).apply_full(inst.sync_revision+1,[payload])
    await session.rollback()
    inst=await session.get(Installation,seeded_domain.installation_a_id)
    with pytest.raises(ValueError,match='registry_only_not_negotiated'):
        await EntitySyncService(session,inst,registry_only_negotiated=True).apply_full(inst.sync_revision+1,[{**payload,'available':True}])
    await session.rollback()
