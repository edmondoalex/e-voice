from uuid import uuid4
import pytest
from apps.cloud_api.app import voice_scope
from apps.cloud_api.app.admin_console import _assign_visible_categories
from apps.cloud_api.app.domain.models import Entity,VoiceCategory
from apps.cloud_api.tests.test_admin_console import _client,_login,_csrf

@pytest.mark.parametrize("endpoint",["assign-selected","bulk-assign","auto-assign"])
async def test_scope_is_rechecked_for_all_category_mutations(session,seeded_domain,monkeypatch,endpoint):
    settings=voice_scope.get_settings().model_copy(update={"voice_selection_v1_enabled":False,"voice_selection_projection_enabled":False})
    monkeypatch.setattr(voice_scope,"get_settings",lambda:settings)
    category=VoiceCategory(tenant_id=seeded_domain.tenant_a_id,slug="temperature",name="Temperature",builtin=True)
    session.add(category)
    def entity(installation,suffix,visible):
        return Entity(installation_id=installation,ha_entity_id="sensor.guard_"+suffix,ha_domain="sensor",friendly_name="guard temperature",device_class="temperature",control_exposed=True,attributes_json={"_ekonex_voice_selected":visible})
    yes=entity(seeded_domain.installation_a_id,"yes",True);no=entity(seeded_domain.installation_a_id,"no",False);foreign=entity(seeded_domain.installation_b_id,"foreign",True)
    session.add_all([yes,no,foreign]);await session.commit()
    client=await _client(session)
    try:
        await _login(client,"owner@example.test","owner-password-123")
        page=await client.get("/voice-categories")
        data={"csrf_token":_csrf(page),"target_category_id":str(category.id),"match":"guard",f"entity_{yes.id}":"1",f"entity_{no.id}":"1",f"entity_{foreign.id}":"1"}
        response=await client.post("/voice-categories/"+endpoint,data=data)
        assert response.status_code==303
        for e in [yes,no,foreign]:await session.refresh(e)
        assert yes.voice_category_id==category.id
        assert no.voice_category_id is None and foreign.voice_category_id is None
    finally:await client.aclose()

async def test_atomic_update_rechecks_revocation_and_tenant(session,seeded_domain,monkeypatch):
    settings=voice_scope.get_settings().model_copy(update={"voice_selection_v1_enabled":False,"voice_selection_projection_enabled":False})
    monkeypatch.setattr(voice_scope,"get_settings",lambda:settings)
    category=VoiceCategory(tenant_id=seeded_domain.tenant_a_id,slug="guardcat",name="Guard",builtin=False)
    row=Entity(installation_id=seeded_domain.installation_a_id,ha_entity_id="sensor.revoked_guard",ha_domain="sensor",control_exposed=True,attributes_json={"_ekonex_voice_selected":True})
    session.add_all([category,row]);await session.commit()
    ids=[row.id];row.attributes_json={"_ekonex_voice_selected":False};await session.flush()
    context=type("Context",(),{"tenant_id":seeded_domain.tenant_a_id})()
    assert await _assign_visible_categories(session,context,ids,category.id)==0
    await session.refresh(row);assert row.voice_category_id is None

async def test_strict_snapshot_membership_rechecked_at_write(session,seeded_domain,monkeypatch):
    from types import SimpleNamespace
    from apps.cloud_api.app.domain.models import ConnectorCredential
    from apps.cloud_api.app.voice_selection_store import register_selection_session,persist_selection
    installation=seeded_domain.installation_a_id;tenant=seeded_domain.tenant_a_id;sid=uuid4()
    row=Entity(installation_id=installation,ha_registry_id='guard-registry',ha_entity_id='sensor.guard_strict',ha_domain='sensor',control_exposed=True)
    credential=ConnectorCredential(installation_id=installation,secret_hash='c'*64)
    category=VoiceCategory(tenant_id=tenant,slug='guard-strict',name='Guard strict',builtin=False)
    session.add_all([row,credential,category]);await session.flush()
    await register_selection_session(session,installation,tenant,sid,credential.id)
    await persist_selection(session,installation,tenant,sid,1,['guard-registry']);await session.commit()
    monkeypatch.setattr(voice_scope,'get_settings',lambda:SimpleNamespace(voice_selection_v1_enabled=True,voice_selection_projection_enabled=True))
    context=type('Context',(),{'tenant_id':tenant})()
    await persist_selection(session,installation,tenant,sid,2,[]);await session.flush()
    assert await _assign_visible_categories(session,context,[row.id],category.id)==0
    await session.refresh(row);assert row.voice_category_id is None
