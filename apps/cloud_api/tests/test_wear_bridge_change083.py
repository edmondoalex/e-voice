import hashlib
import hmac
import time
import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.cloud_api.app.database import get_database_session
from apps.cloud_api.app.domain.models import Entity, WearDevice, WearNotification
from apps.cloud_api.app.main import app
from apps.cloud_api.app import main as main_module
from apps.cloud_api.app import wear_wake_worker
from apps.cloud_api.app.wear_bridge import WearBridgeAuthority, WearBridgeWake, canonical, derive_installation_key, lock_wear_writer
from apps.cloud_api.app.wear_wake_worker import dispatch_one
from apps.cloud_api.app.wear_cutover import CutoverBlocked, committed_cutover_receipt, drain_proof, import_proof, installation_snapshot, restore_legacy, transfer_legacy_to_econtrol
from apps.cloud_api.app.wear_notification_rules import evaluate_wear_notification_rules
from types import SimpleNamespace
import pytest
from ownership import Blocked, Store
from econtrol_wear_sender import deliver_one
from wear_events import WearEvents
from verified_import import verified_import, signature, canonical as import_canonical
from verified_rules import VerifiedRules


def test_dedicated_derived_key_is_installation_scoped():
    first,second=uuid4(),uuid4()
    master=b'synthetic-bridge-master-key-32-bytes'
    assert derive_installation_key(master,first)==derive_installation_key(master,first)
    assert derive_installation_key(master,first)!=derive_installation_key(master,second)
    with pytest.raises(ValueError): derive_installation_key(b'short',first)


async def test_endpoints_refresh_stale_entities_and_device(session, seeded_domain, monkeypatch):
    from control_client import ControlClient
    from apps.cloud_api.app.domain.models import Installation
    installation=seeded_domain.installation_a_id;revision='a'*64;key=b'k'*32
    entity=await session.get(Entity,seeded_domain.entity_a_id)
    entity.ha_entity_id='sensor.before';entity.ha_registry_id='registry-1';entity.voice_name='Before'
    device=WearDevice(credential_hash='a'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=installation,display_name='Watch',capabilities_json=['notification.receive'],
        notification_preferences_json={'enabled':True,'categories':['system'],'minimum_severity':'info'})
    session.add(device);session.add(WearBridgeAuthority(installation_id=installation,owner='econtrol',generation=1,import_revision=revision))
    await session.commit();entity_id=entity.id;device_id=device.id
    factory=async_sessionmaker(session.bind,expire_on_commit=False)
    async with factory() as writer:
        changed=await writer.get(Entity,entity_id);changed.ha_entity_id='sensor.after';changed.voice_name='After'
        inst=await writer.get(Installation,installation);inst.sync_revision+=1
        await writer.commit()
    assert entity.ha_entity_id=='sensor.before' and entity.voice_name=='Before'
    async def override():yield session
    async def provider(value):return key if value==installation else None
    app.dependency_overrides[get_database_session]=override;app.state.wear_bridge_key_provider=provider
    settings=SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_control_enabled=True,wear_render_v2_enabled=True,wear_bridge_scope={})
    monkeypatch.setattr('apps.cloud_api.app.wear_control_service.get_settings',lambda:settings)
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:settings)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            async def send(op,payload):
                response=await client.post('/service/wear/control/'+op,json=payload)
                assert response.status_code==200
                return response.json()
            control=ControlClient(str(installation),key,send)
            mapping=await control.call('mapping',{'generation':1,'revision':revision,'entity_ids':[str(entity_id)]})
            assert mapping['statement']['mapping'][str(entity_id)]['entity_id']=='sensor.after'
            async with factory() as writer:
                changed=await writer.get(Entity,entity_id);changed.voice_name='Newest';await writer.commit()
            assert entity.voice_name=='After'
            now=int(time.time());claims={'issuer':'econtrol','audience':'evoice-wear-service','installation':str(installation),
                'scope':'wear.notifications.enqueue','method':'POST','path':'/service/wear/notifications',
                'issued':now,'expires':now+30,'nonce':'a'*32}
            body={'event_id':'a'*64,'generation':1,'device_id':str(device_id),'category':'system','severity':'info','title':'legacy','body':'legacy',
                'render':{'entity_id':str(entity_id),'ha_entity_id':'sensor.after','registry_id':'registry-1',
                    'previous_state':'off','state':'on','available':True,'title_template':'{nome}','body_template':'{nome}'}}
            def envelope(c,b):return {'claims':c,'body':b,'signature':hmac.new(key,canonical(c,b),hashlib.sha256).hexdigest()}
            assert (await client.post('/service/wear/notifications',json=envelope(claims,body))).status_code==200
            notification=(await session.scalars(select(WearNotification).where(WearNotification.device_id==device_id))).one()
            assert notification.title=='Newest'
            stale_body={**body,'event_id':'b'*64,'render':{**body['render'],'ha_entity_id':'sensor.before'}}
            stale=await client.post('/service/wear/notifications',json=envelope({**claims,'nonce':'b'*32},stale_body))
            assert stale.status_code==409
            await session.refresh(device)
            async with factory() as writer:
                changed_device=await writer.get(WearDevice,device_id);changed_device.capabilities_json=[];await writer.commit()
            assert 'notification.receive' in device.capabilities_json
            denied=await client.post('/service/wear/notifications',json=envelope({**claims,'nonce':'c'*32},{**body,'event_id':'c'*64}))
            assert denied.status_code==404
    finally:
        app.dependency_overrides.pop(get_database_session,None);app.state.wear_bridge_key_provider=None


async def test_worker_start_requires_both_flags(monkeypatch):
    async def startup_check(*args,**kwargs):pass
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.verify_bridge_startup',startup_check)
    started=asyncio.Event();stopped=asyncio.Event()
    async def worker(_factory):
        started.set()
        try: await asyncio.Event().wait()
        finally: stopped.set()
    monkeypatch.setattr(wear_wake_worker,'run_worker',worker)
    monkeypatch.setattr(main_module,'get_settings',lambda:SimpleNamespace(environment='production',
        wear_bridge_fence_enabled=False,wear_bridge_wake_worker_enabled=True))
    async with main_module.lifespan(app): await asyncio.sleep(0)
    assert not started.is_set()
    monkeypatch.setattr(main_module,'get_settings',lambda:SimpleNamespace(environment='production',
        wear_bridge_fence_enabled=True,wear_bridge_wake_worker_enabled=True))
    async with main_module.lifespan(app): await asyncio.wait_for(started.wait(),1)
    assert stopped.is_set()


async def test_writer_fence_blocks_legacy_edits_but_allows_revocation(session,seeded_domain,monkeypatch):
    installation=seeded_domain.installation_a_id
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_scope={str(seeded_domain.tenant_a_id):[str(installation)]}))
    with pytest.raises(HTTPException) as missing:
        await lock_wear_writer(session,installation)
    assert missing.value.status_code==503
    session.add(WearBridgeAuthority(installation_id=installation,owner='econtrol',generation=1));await session.commit()
    with pytest.raises(HTTPException) as blocked:
        await lock_wear_writer(session,installation)
    assert blocked.value.status_code==409
    await lock_wear_writer(session,installation,allow_econtrol=True)
    await session.rollback()


async def test_snapshot_refreshes_device_and_entity_from_other_session(session,seeded_domain):
    installation=seeded_domain.installation_a_id
    entity=await session.get(Entity,seeded_domain.entity_a_id)
    entity.ha_registry_id='registry';entity.ha_entity_id='sensor.before';entity.voice_name='Before'
    device=WearDevice(credential_hash='d'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=installation,display_name='Before',capabilities_json=[],
        notification_preferences_json={'quick_actions':[{'id':'a','entity_id':str(entity.id)}]})
    session.add(device);session.add(WearBridgeAuthority(installation_id=installation,owner='legacy',generation=0));await session.commit()
    factory=async_sessionmaker(session.bind,expire_on_commit=False)
    async with factory() as writer:
        remote_entity=await writer.get(Entity,entity.id);remote_entity.ha_entity_id='sensor.after';remote_entity.voice_name='After'
        remote_device=await writer.get(WearDevice,device.id);remote_device.display_name='After'
        remote_device.notification_preferences_json={'quick_actions':[{'id':'b','entity_id':str(entity.id)}]}
        await writer.commit()
    assert entity.ha_entity_id=='sensor.before' and device.display_name=='Before'
    await session.scalar(select(WearBridgeAuthority).where(WearBridgeAuthority.installation_id==installation).with_for_update())
    snapshot,_=await installation_snapshot(session,installation)
    assert snapshot['devices'][0]['name']=='After'
    assert snapshot['devices'][0]['quick_actions'][0]['id']=='b'
    assert snapshot['entity_mapping'][str(entity.id)]['entity_id']=='sensor.after'
    assert snapshot['entity_mapping'][str(entity.id)]['display_name']=='After'


async def test_cutover_cas_is_disabled_by_default_and_idempotent(session,seeded_domain):
    installation=seeded_domain.installation_a_id
    authority=WearBridgeAuthority(installation_id=installation,owner='legacy',generation=4)
    session.add(authority);await session.commit()
    key=b'synthetic-cutover-key-at-least-32-bytes'; revision='a'*64
    proof=import_proof(key,installation,4,revision)
    with pytest.raises(CutoverBlocked,match='not_qualified'):
        await transfer_legacy_to_econtrol(session,installation,4,revision,revision,proof,key)
    with pytest.raises(CutoverBlocked,match='import_unverified'):
        await transfer_legacy_to_econtrol(session,installation,4,revision,'b'*64,proof,key,qualified=True)
    device=WearDevice(credential_hash='c'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=installation,display_name='Synthetic',capabilities_json=[],notification_preferences_json={})
    session.add(device);await session.flush();device_id=device.id
    session.add(WearBridgeWake(device_id=device_id,event_key='pending',attempts=0));await session.commit()
    _, revision=await installation_snapshot(session,installation)
    proof=import_proof(key,installation,4,revision)
    with pytest.raises(CutoverBlocked,match='pending_wake'):
        await transfer_legacy_to_econtrol(session,installation,4,revision,revision,proof,key,qualified=True)
    pending=await session.get(WearBridgeWake,(device_id,'pending'));await session.delete(pending);await session.commit()
    device.notification_preferences_json={'quick_actions':[{'id':'new'}]};await session.commit()
    with pytest.raises(CutoverBlocked,match='snapshot_changed'):
        await transfer_legacy_to_econtrol(session,installation,4,revision,revision,proof,key,qualified=True)
    device.notification_preferences_json={};await session.commit()
    assert await transfer_legacy_to_econtrol(session,installation,4,revision,revision,proof,key,qualified=True)==5
    with pytest.raises(CutoverBlocked,match='fresh_session_required'):
        await committed_cutover_receipt(session,installation,5,revision,key)
    await session.commit()
    signed=await committed_cutover_receipt(session,installation,5,revision,key)
    assert signed==hmac.new(key,b'ekonex-wear-cutover-v1\0'+installation.bytes+(5).to_bytes(8,'big')+revision.encode(),hashlib.sha256).hexdigest()
    assert await transfer_legacy_to_econtrol(session,installation,4,revision,revision,proof,key,qualified=True)==5
    with pytest.raises(CutoverBlocked,match='stale_authority'):
        await transfer_legacy_to_econtrol(session,installation,5,revision,revision,import_proof(key,installation,5,revision),key,qualified=True)
    await session.rollback()
    with pytest.raises(CutoverBlocked,match='drain_unverified'):
        await restore_legacy(session,installation,5,revision,proof,key,qualified=True)
    receipt=drain_proof(key,installation,5,revision)
    assert await restore_legacy(session,installation,5,revision,receipt,key,qualified=True)==6
    await session.commit()
    assert await restore_legacy(session,installation,5,revision,receipt,key,qualified=True)==6
    await session.rollback()


async def test_receiver_real_asgi_database_and_fencing(session,seeded_domain,monkeypatch):
    installation=seeded_domain.installation_a_id
    device=WearDevice(credential_hash='e'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=installation,display_name='Synthetic',capabilities_json=['notification.receive'],
        notification_preferences_json={'enabled':True,'categories':['system'],'minimum_severity':'info'},
        fcm_token_encrypted='synthetic-encrypted-token')
    session.add(device);session.add(WearBridgeAuthority(installation_id=installation,owner='econtrol',generation=1));await session.commit()
    device_id=device.id
    async def override(): yield session
    app.dependency_overrides[get_database_session]=override
    key=b'synthetic-only-bridge-key-32-bytes'
    async def provider(value): return key if value==installation else None
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_render_v2_enabled=True))
    now=int(time.time())
    claims={'issuer':'econtrol','audience':'evoice-wear-service','installation':str(installation),
        'scope':'wear.notifications.enqueue','method':'POST','path':'/service/wear/notifications',
        'issued':now,'expires':now+30,'nonce':'a'*32}
    body={'event_id':'a'*64,'generation':1,'device_id':str(device_id),'category':'system','severity':'info','title':'Test','body':'Synthetic'}
    def envelope(c=claims,b=body): return {'claims':c,'body':b,'signature':hmac.new(key,canonical(c,b),hashlib.sha256).hexdigest()}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            app.state.wear_bridge_key_provider=None
            assert (await client.post('/service/wear/notifications',json=envelope())).status_code==503
            app.state.wear_bridge_key_provider=provider
            monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=False))
            assert (await client.post('/service/wear/notifications',json=envelope())).status_code==503
            monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_render_v2_enabled=True))
            assert (await client.post('/service/wear/notifications',json={**envelope(),'signature':'0'*64})).status_code==403
            assert (await client.post('/service/wear/notifications',json=envelope())).status_code==200
            assert (await client.post('/service/wear/notifications',json=envelope())).status_code==403
            retry={**claims,'nonce':'b'*32}
            assert (await client.post('/service/wear/notifications',json=envelope(retry))).status_code==200
            changed={**body,'title':'tampered'}
            assert (await client.post('/service/wear/notifications',json=envelope({**claims,'nonce':'c'*32},changed))).status_code==409
            fenced={**body,'event_id':'b'*64,'generation':2}
            assert (await client.post('/service/wear/notifications',json=envelope({**claims,'nonce':'d'*32},fenced))).status_code==409
            other=await session.get(Entity,seeded_domain.entity_b_id)
            foreign={**body,'event_id':'c'*64,'render':{'entity_id':str(other.id),'ha_entity_id':other.ha_entity_id,
                'registry_id':'foreign-registry','previous_state':'off','state':'on','available':True,'title_template':None,'body_template':None}}
            assert (await client.post('/service/wear/notifications',json=envelope({**claims,'nonce':'e'*32},foreign))).status_code==409
            rows=(await session.scalars(select(WearNotification).where(WearNotification.device_id==device_id))).all()
            assert len(rows)==1 and rows[0].event_key=='econtrol:'+'a'*64
            assert len((await session.scalars(select(WearBridgeWake))).all())==1
            class Sender:
                def __init__(self): self.attempts=0
                async def send_wake(self,token,event):
                    self.attempts+=1
                    if self.attempts==1: raise RuntimeError('synthetic outage')
                    return True
            sender=Sender()
            assert await dispatch_one(session,sender) is False
            assert (await session.scalars(select(WearBridgeWake))).one().attempts==1
            assert await dispatch_one(session,sender) is True
            assert (await session.scalars(select(WearBridgeWake))).all()==[]
    finally:
        app.dependency_overrides.pop(get_database_session,None)
        app.state.wear_bridge_key_provider=None


async def test_legacy_engine_obeys_fence_and_persists_wake(session,seeded_domain,monkeypatch):
    monkeypatch.setattr('apps.cloud_api.app.wear_notification_rules.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_render_v2_enabled=True))
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_scope={}))
    installation=seeded_domain.installation_a_id
    entity=(await session.scalars(select(Entity).where(Entity.installation_id==installation))).first()
    assert entity is not None
    entity.state='on';entity.available=True
    entity_id=entity.id
    device=WearDevice(credential_hash='f'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=installation,display_name='Synthetic',capabilities_json=['notification.receive'],
        notification_preferences_json={'enabled':True,'categories':['system'],'minimum_severity':'info',
            'notification_rules':[{'id':'test-rule','entity_id':str(entity_id),'trigger':'any_change'}]},
        fcm_token_encrypted='synthetic-encrypted-token')
    authority=WearBridgeAuthority(installation_id=installation,owner='econtrol',generation=1)
    session.add_all([device,authority]);await session.commit()
    assert await evaluate_wear_notification_rules(session,installation,[(entity_id,'off',True)])==0
    assert (await session.scalars(select(WearNotification))).all()==[]
    authority=await session.get(WearBridgeAuthority,installation);authority.owner='legacy';await session.commit()
    assert await evaluate_wear_notification_rules(session,installation,[(entity_id,'off',True)])==1
    assert len((await session.scalars(select(WearBridgeWake))).all())==1


async def test_real_producer_drain_proof_controls_receiver_rollback(session,seeded_domain,tmp_path):
    installation=seeded_domain.installation_a_id; key=b'synthetic-cutover-key-at-least-32-bytes'; revision='c'*64
    producer=Store(tmp_path/'producer.sqlite3')
    producer.initialize_legacy(str(installation))
    producer.import_snapshot(str(installation),revision,{'devices':[]})
    assert producer.transfer(str(installation),'legacy',0,'econtrol')==1
    event=producer.enqueue(str(installation),1,'watch','rule','event',{'title':'synthetic'})
    with pytest.raises(Blocked,match='unresolved_outbox'):
        producer.freeze_and_sign_drain(str(installation),1,revision,key)
    producer.acknowledge(event)
    receipt=producer.freeze_and_sign_drain(str(installation),1,revision,key)
    assert receipt==producer.freeze_and_sign_drain(str(installation),1,revision,key)
    with pytest.raises(Blocked,match='stale_fence'):
        producer.enqueue(str(installation),1,'watch','rule','later',{'title':'synthetic'})
    session.add(WearBridgeAuthority(installation_id=installation,owner='econtrol',generation=1,import_revision=revision))
    await session.commit()
    assert await restore_legacy(session,installation,1,revision,receipt,key,qualified=True)==2
    await session.commit();producer.close()


async def test_econtrol_to_evoice_real_asgi_retries_without_duplicate(session,seeded_domain,tmp_path,monkeypatch):
    installation=seeded_domain.installation_a_id;key=b'synthetic-bridge-key-at-least-32-bytes'
    device=WearDevice(credential_hash='d'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=installation,display_name='Synthetic',capabilities_json=['notification.receive'],
        notification_preferences_json={'enabled':True,'categories':['system'],'minimum_severity':'info'})
    session.add(device);session.add(WearBridgeAuthority(installation_id=installation,owner='econtrol',generation=1));await session.commit()
    device_id=device.id
    producer=Store(tmp_path/'producer-e2e.sqlite3');producer.initialize_legacy(str(installation))
    entity=await session.get(Entity,seeded_domain.entity_a_id)
    entity.ha_entity_id='sensor.synthetic';entity.ha_registry_id='synthetic';entity.voice_name='Nome personalizzato';await session.commit()
    source_id=str(entity.id)
    snapshot={'devices':[{'device_id':str(device_id),'user_id':str(seeded_domain.user_a_id),'name':'Watch',
        'notification_rules':[{'id':'rule','entity_id':source_id,'enabled':True,'trigger':'becomes',
        'target_state':'on','category':'system','severity':'info','title':'{nome} / {id}','body':'{valore_precedente} ? {valore}'}], 'quick_actions':[]}],
        'entity_mapping':{source_id:{'entity_id':'sensor.synthetic','registry_id':'synthetic','display_name':'Nome personalizzato'}}}
    revision=hashlib.sha256(import_canonical(snapshot)).hexdigest();now=int(time.time())
    claims={'issuer':'evoice','audience':'econtrol','scope':'wear.snapshot.full','installation':str(installation),
        'generation':0,'revision':revision,'issued':now,'expires':now+30}
    verified_import(producer,{'claims':claims,'snapshot':snapshot,'signature':signature(key,claims,snapshot)},key,
        installation=installation,generation=0,now=now)
    producer.import_snapshot(str(installation),revision,{'devices':[]});producer.transfer(str(installation),'legacy',0,'econtrol')
    rules=VerifiedRules(producer,str(installation),1,revision,qualified=True)
    # This isolated listener fixture explicitly supplies a valid gate; real refresh is tested separately.
    producer.db.execute('CREATE TABLE wear_stream_gate (installation TEXT PRIMARY KEY,generation INTEGER NOT NULL,state TEXT NOT NULL)')
    producer.db.execute("INSERT INTO wear_stream_gate VALUES (?,1,'running')",(str(installation),))
    from fresh_authority import FreshAuthority
    from fresh_render_capability import FreshRenderCapability
    import json
    authority=FreshAuthority(key,str(installation),1,revision)
    statement={'installation':str(installation),'owner':'econtrol','generation':1,'revision':revision,'nonce':'p'*32,'issued':now,'expires':now+15}
    sig=hmac.new(key,b'ekonex-wear-current-authority-v1\0'+json.dumps(statement,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
    authority.accept({'statement':statement,'receipt':sig},'p'*32)
    capability=FreshRenderCapability(key,authority)
    statement={**statement,'nonce':'q'*32,'render_v2':True}
    sig=hmac.new(key,b'ekonex-wear-capabilities-v1\0'+json.dumps(statement,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
    capability.accept({'statement':statement,'receipt':sig},'q'*32)
    from mapping_overlay import MappingOverlay
    overlay=MappingOverlay(producer,key,authority)
    mapping_statement={'installation':str(installation),'generation':1,'revision':revision,'mapping_revision':0,
        'nonce':'r'*32,'issued':now,'expires':now+15,'mapping':{source_id:{'entity_id':'sensor.synthetic','registry_id':'synthetic'}}}
    sig=hmac.new(key,b'ekonex-wear-mapping-v1\0'+json.dumps(mapping_statement,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
    overlay.reconcile({'statement':mapping_statement,'receipt':sig},'r'*32)
    producer.db.execute("UPDATE wear_stream_gate SET state='running' WHERE installation=?",(str(installation),))
    listener=WearEvents(producer,str(installation),1,rules,enabled=True,authority_guard=authority.valid,render_v2_enabled=True,render_capability=capability,mapping_guard=overlay)
    before={'entity_id':'sensor.synthetic','state':'off','last_updated':'2026-11-20T10:00:00+00:00','attributes':{},'context':{'id':'a'}}
    after={'entity_id':'sensor.synthetic','state':'on','last_updated':'2026-11-20T10:00:01+00:00','attributes':{},'context':{'id':'b'}}
    message={'id':1,'type':'event','event':{'event_type':'state_changed','data':{'entity_id':'sensor.synthetic','old_state':before,'new_state':after}}}
    event,=listener.accept(message)
    assert listener.accept(message)==[event]
    # Change after event enqueue: delivery must resolve the latest vocal name.
    entity.voice_name='Nome aggiornato';await session.commit()
    async def override(): yield session
    async def provider(value): return key if value==installation else None
    app.dependency_overrides[get_database_session]=override;app.state.wear_bridge_key_provider=provider
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_render_v2_enabled=True))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            async def lost_response(payload):
                response=await client.post('/service/wear/notifications',json=payload)
                assert response.status_code==200
                raise TimeoutError('simulated lost receipt')
            with pytest.raises(TimeoutError):
                await deliver_one(producer,str(installation),1,key,lost_response,nonce='e'*32)
            assert producer.next_pending(str(installation),1)[0]==event
            device.revoked_at=datetime.now(UTC);entity.voice_name='Nome successivo';await session.commit()
            async def delivered(payload):
                response=await client.post('/service/wear/notifications',json=payload)
                assert response.status_code==200
                return response.json()
            assert await deliver_one(producer,str(installation),1,key,delivered,nonce='f'*32)==event
            assert producer.next_pending(str(installation),1) is None
            notifications=(await session.scalars(select(WearNotification).where(WearNotification.device_id==device_id))).all()
            assert len(notifications)==1
            assert notifications[0].title=='Nome aggiornato / sensor.synthetic'
            assert notifications[0].body=='off ? on'
    finally:
        app.dependency_overrides.pop(get_database_session,None);app.state.wear_bridge_key_provider=None;producer.close()


async def test_failed_fcm_does_not_erase_new_token(session,seeded_domain):
    device=WearDevice(credential_hash='b'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,display_name='Synthetic',capabilities_json=['notification.receive'],
        notification_preferences_json={},fcm_token_encrypted='old-token')
    session.add(device);await session.flush();device_id=device.id
    session.add(WearBridgeWake(device_id=device_id,event_key='event',attempts=0));await session.commit()
    class Sender:
        async def send_wake(self,token,event):
            assert token=='old-token'
            await session.execute(update(WearDevice).where(WearDevice.id==device_id)
                .values(fcm_token_encrypted='new-token').execution_options(synchronize_session=False))
            return False
    assert await dispatch_one(session,Sender()) is False
    token=await session.scalar(select(WearDevice.fcm_token_encrypted).where(WearDevice.id==device_id))
    assert token=='new-token'
    assert (await session.scalars(select(WearBridgeWake))).one().attempts==1


async def test_successful_fcm_retries_when_token_changed(session,seeded_domain):
    device=WearDevice(credential_hash='9'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,display_name='Synthetic',capabilities_json=['notification.receive'],
        notification_preferences_json={},fcm_token_encrypted='old-token')
    session.add(device);await session.flush();device_id=device.id
    session.add(WearBridgeWake(device_id=device_id,event_key='event',attempts=0));await session.commit()
    class Sender:
        async def send_wake(self,token,event):
            await session.execute(update(WearDevice).where(WearDevice.id==device_id)
                .values(fcm_token_encrypted='new-token').execution_options(synchronize_session=False))
            return True
    assert await dispatch_one(session,Sender()) is False
    assert await session.scalar(select(WearDevice.fcm_token_encrypted).where(WearDevice.id==device_id))=='new-token'
    assert (await session.scalars(select(WearBridgeWake))).one() is not None


async def test_control_service_snapshot_cutover_and_rollback(session,seeded_domain,tmp_path,monkeypatch):
    from cutover_authority import activate_after_remote_commit, finalize_legacy_after_remote_commit
    from control_client import ControlClient
    installation=seeded_domain.installation_a_id; key=b'synthetic-bridge-key-at-least-32-bytes'
    session.add(WearBridgeAuthority(installation_id=installation,owner='legacy',generation=0));await session.commit()
    store=Store(tmp_path/'control-e2e.sqlite');store.initialize_legacy(str(installation))
    async def override(): yield session
    async def provider(value): return key if value==installation else None
    app.dependency_overrides[get_database_session]=override;app.state.wear_bridge_key_provider=provider
    monkeypatch.setattr('apps.cloud_api.app.wear_control_service.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_control_enabled=True))
    def envelope(operation,body,nonce):
        now=int(time.time());path='/service/wear/control/'+operation
        claims={'issuer':'econtrol','audience':'evoice-wear-service','installation':str(installation),'scope':'wear.control.'+operation,
                'method':'POST','path':path,'issued':now,'expires':now+30,'nonce':nonce*32}
        return {'claims':claims,'body':body,'signature':hmac.new(key,canonical(claims,body),hashlib.sha256).hexdigest()}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            req=envelope('snapshot',{},'a')
            monkeypatch.setattr('apps.cloud_api.app.wear_control_service.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_control_enabled=False))
            assert (await client.post('/service/wear/control/snapshot',json=req)).status_code==503
            monkeypatch.setattr('apps.cloud_api.app.wear_control_service.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_control_enabled=True))
            forged={**req,'signature':'0'*64}
            assert (await client.post('/service/wear/control/snapshot',json=forged)).status_code==403
            response=await client.post('/service/wear/control/snapshot',json=req)
            assert response.status_code==200,response.text
            assert (await client.post('/service/wear/control/snapshot',json=req)).status_code==403
            lost={'cutover','rollback'}
            async def send_control(operation,payload):
                result=await client.post('/service/wear/control/'+operation,json=payload)
                assert result.status_code==200,result.text
                if operation in lost:
                    lost.remove(operation);raise TimeoutError('synthetic lost response')
                return result.json()
            control_client=ControlClient(str(installation),key,send_control)
            snapshot=await control_client.call('snapshot',{})
            imported=verified_import(store,snapshot,key,installation=installation,generation=0)
            revision=imported['revision'];body={'generation':0,'revision':revision,'proof':imported['receipt']}
            with pytest.raises(Blocked,match='control_call_failed'): await control_client.call('cutover',body)
            remote=await control_client.call('cutover',body)
            assert (remote['owner'],remote['generation'])==('econtrol',1)
            status=await client.post('/service/wear/control/authority',json=envelope('authority',{},'d'))
            assert status.status_code==200
            current=status.json();statement=current['statement']
            assert (statement['owner'],statement['generation'],statement['revision'],statement['nonce'])==('econtrol',1,revision,'d'*32)
            payload=__import__('json').dumps(statement,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
            assert current['receipt']==hmac.new(key,b'ekonex-wear-current-authority-v1\0'+payload,hashlib.sha256).hexdigest()
            from fresh_authority import FreshAuthority
            from authority_refresh import AuthorityRefresh
            fresh=FreshAuthority(key,str(installation),1,revision)
            async def send_authority(payload):
                result=await client.post('/service/wear/control/authority',json=payload)
                assert result.status_code==200
                return result.json()
            refresh=AuthorityRefresh(fresh,str(installation),key,send_authority)
            await refresh.refresh();assert fresh.valid()
            assert activate_after_remote_commit(store,str(installation),1,revision,remote['receipt'],key,qualified=True,current_authority=fresh)
            assert not activate_after_remote_commit(store,str(installation),1,revision,remote['receipt'],key,qualified=True,current_authority=fresh)
            receipt=store.freeze_and_sign_drain(str(installation),1,revision,key)
            with pytest.raises(Blocked,match='control_call_failed'):
                await control_client.call('rollback',{'generation':1,'revision':revision,'proof':receipt})
            remote=await control_client.call('rollback',{'generation':1,'revision':revision,'proof':receipt})
            assert (remote['owner'],remote['generation'])==('legacy',2)
            with pytest.raises(Blocked,match='authority_refresh_failed'): await refresh.refresh()
            assert not fresh.valid()
            legacy=FreshAuthority(key,str(installation),2,revision,owner='legacy')
            legacy_refresh=AuthorityRefresh(legacy,str(installation),key,send_authority)
            await legacy_refresh.refresh()
            assert finalize_legacy_after_remote_commit(store,str(installation),1,revision,legacy,key,qualified=True)
            assert store.db.execute('SELECT owner,generation FROM ownership WHERE installation=?',(str(installation),)).fetchone()==('legacy',2)
    finally:
        app.dependency_overrides.pop(get_database_session,None);app.state.wear_bridge_key_provider=None;store.close()

async def test_lifespan_secret_provider_is_explicit_and_cleared(monkeypatch):
    async def startup_check(*args,**kwargs):pass
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.verify_bridge_startup',startup_check)
    import base64
    monkeypatch.setattr(main_module,'get_settings',lambda:SimpleNamespace(environment='production',wear_bridge_fence_enabled=True,wear_bridge_wake_worker_enabled=False))
    monkeypatch.delenv('EKONEX_WEAR_BRIDGE_MASTER_B64',raising=False)
    async with main_module.lifespan(app): assert app.state.wear_bridge_key_provider is None
    monkeypatch.setenv('EKONEX_WEAR_BRIDGE_MASTER_B64',base64.b64encode(b'x'*32).decode())
    async with main_module.lifespan(app):
        assert len(await app.state.wear_bridge_key_provider(uuid4()))==32
    assert app.state.wear_bridge_key_provider is None

async def test_control_key_rotation_overlap(session,seeded_domain,monkeypatch,tmp_path):
    import base64
    from apps.cloud_api.app.wear_key_provider import cloud_provider
    installation=seeded_domain.installation_a_id;old=b'o'*32;new=b'n'*32
    config={'wear_bridge_master_b64':base64.b64encode(new).decode(),
            'wear_bridge_previous_master_b64':base64.b64encode(old).decode()}
    overlap=cloud_provider(config);keys=await overlap(installation)
    assert isinstance(keys,tuple) and len(keys)==2
    # The test supplies explicit independent recipient and signer identities.
    import sqlite3
    from cryptography.hazmat.primitives.asymmetric import ed25519,x25519
    from wear_provision import create_envelope,accept_envelope
    from wear_key_provider import econtrol_provider
    recipient=x25519.X25519PrivateKey.generate();signer=ed25519.Ed25519PrivateKey.generate()
    provision_db=sqlite3.connect(tmp_path/'provision.db',isolation_level=None)
    sealed=create_envelope(seeded_domain.tenant_a_id,installation,1,keys[0],recipient.public_key(),signer)
    provisioned=accept_envelope(provision_db,sealed,seeded_domain.tenant_a_id,installation,recipient,signer.public_key());provision_db.close()
    edge=econtrol_provider({'wear_bridge_installation':str(installation),'wear_bridge_installation_key_b64':base64.b64encode(provisioned).decode()})
    assert await edge(installation)==keys[0]
    session.add(WearBridgeAuthority(installation_id=installation,owner='legacy',generation=0));await session.commit()
    async def override():yield session
    app.dependency_overrides[get_database_session]=override;app.state.wear_bridge_key_provider=overlap
    monkeypatch.setattr('apps.cloud_api.app.wear_control_service.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_control_enabled=True))
    def request(key,nonce):
        now=int(time.time());claims={'issuer':'econtrol','audience':'evoice-wear-service','installation':str(installation),
          'scope':'wear.control.authority','method':'POST','path':'/service/wear/control/authority',
          'issued':now,'expires':now+30,'nonce':nonce*32};body={}
        return {'claims':claims,'body':body,'signature':hmac.new(key,canonical(claims,body),hashlib.sha256).hexdigest()}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            for key,nonce in ((keys[0],'a'),(keys[1],'b')):
                response=await client.post('/service/wear/control/authority',json=request(key,nonce));assert response.status_code==200
                statement=response.json()['statement'];raw=__import__('json').dumps(statement,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
                assert response.json()['receipt']==hmac.new(key,b'ekonex-wear-current-authority-v1\0'+raw,hashlib.sha256).hexdigest()
            app.state.wear_bridge_key_provider=cloud_provider({'wear_bridge_master_b64':config['wear_bridge_master_b64']})
            assert (await client.post('/service/wear/control/authority',json=request(keys[1],'c'))).status_code==403
            assert (await client.post('/service/wear/control/authority',json=request(keys[0],'d'))).status_code==200
    finally:
        app.dependency_overrides.pop(get_database_session,None);app.state.wear_bridge_key_provider=None

async def test_provision_approval_requires_fresh_membership_and_installation(session,seeded_domain):
    from apps.cloud_api.app.domain.enums import TenantRole,InstallationStatus
    from apps.cloud_api.app.domain.models import Installation
    from apps.cloud_api.app.wear_provision_approval import ChallengeDenied,verify_tenant_approval
    installation=await session.get(Installation,seeded_domain.installation_a_id);installation.status=InstallationStatus.ACTIVE;await session.commit()
    challenge={'challenge_id':str(uuid4()),'nonce':'n'*32,'issued':100,'expires':200,'tenant':str(seeded_domain.tenant_a_id),
        'installer_installation':str(uuid4()),'hub_ref':'hub-fixture','evoice_installation':str(installation.id),'scope':'wear.bridge.provision'}
    args=dict(authenticated_user=seeded_domain.user_a_id,tenant=seeded_domain.tenant_a_id,challenge=challenge,allowed_roles=frozenset({TenantRole.OWNER}),now=110)
    assert (await verify_tenant_approval(session,**args))['installation']==str(installation.id)
    for changes in ({'authenticated_user':seeded_domain.user_readonly_id},{'authenticated_user':seeded_domain.user_b_id},
                    {'tenant':seeded_domain.tenant_b_id},{'allowed_roles':frozenset()},{'now':200}):
        with pytest.raises(ChallengeDenied): await verify_tenant_approval(session,**{**args,**changes})
    wrong={**challenge,'evoice_installation':str(seeded_domain.installation_b_id)}
    with pytest.raises(ChallengeDenied):await verify_tenant_approval(session,**{**args,'challenge':wrong})
    installation.status=InstallationStatus.REVOKED;await session.commit()
    with pytest.raises(ChallengeDenied):await verify_tenant_approval(session,**args)

async def test_installer_challenge_matches_evoice_tenant_approval(session,seeded_domain,tmp_path):
    from provisioning_plan import ProvisioningPlan
    from apps.cloud_api.app.domain.enums import TenantRole,InstallationStatus
    from apps.cloud_api.app.domain.models import Installation
    from apps.cloud_api.app.wear_provision_approval import ChallengeDenied,verify_tenant_approval
    installation=await session.get(Installation,seeded_domain.installation_a_id);installation.status=InstallationStatus.ACTIVE;await session.commit()
    orchestrator=ProvisioningPlan(tmp_path/'provision-metadata.sqlite')
    binding={'installation_id':str(uuid4()),'hub_instance_ref':'hub-fixture'}
    try:
        challenge=orchestrator.begin(binding,str(installation.id),str(seeded_domain.tenant_a_id),b'synthetic-admin-proof',
            lambda *a:True,lambda *a:True,now=100)
        args={'authenticated_user':seeded_domain.user_a_id,'tenant':seeded_domain.tenant_a_id,'challenge':challenge,
              'allowed_roles':frozenset({TenantRole.OWNER}),'now':110}
        assert (await verify_tenant_approval(session,**args))['installation']==str(installation.id)
        with pytest.raises(ChallengeDenied):await verify_tenant_approval(session,**{**args,'tenant':seeded_domain.tenant_b_id})
        installation.status=InstallationStatus.REVOKED;await session.commit()
        with pytest.raises(ChallengeDenied):await verify_tenant_approval(session,**args)
    finally:orchestrator.close()


async def test_scoped_bootstrap_preserves_other_tenants_and_rollback(session,seeded_domain,monkeypatch):
    from apps.cloud_api.app.wear_bridge import bootstrap_wear_authority
    from apps.cloud_api.app.domain.models import Installation
    from apps.cloud_api.app.domain.enums import InstallationStatus
    a,b=seeded_domain.installation_a_id,seeded_domain.installation_b_id
    instance=await session.get(Installation,a);instance.status=InstallationStatus.ACTIVE;await session.commit()
    settings=SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_scope={str(seeded_domain.tenant_a_id):[str(a)]})
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:settings)
    assert await lock_wear_writer(session,b) is None
    with pytest.raises(HTTPException) as missing:await lock_wear_writer(session,a)
    assert missing.value.status_code==503
    with pytest.raises(HTTPException):await bootstrap_wear_authority(session,seeded_domain.tenant_b_id,a,qualified=True)
    with pytest.raises(HTTPException):await bootstrap_wear_authority(session,seeded_domain.tenant_a_id,a)
    created=await bootstrap_wear_authority(session,seeded_domain.tenant_a_id,a,qualified=True)
    assert (created.owner,created.generation)==('legacy',0)
    await session.rollback()
    with pytest.raises(HTTPException) as missing:await lock_wear_writer(session,a)
    assert missing.value.status_code==503
    await bootstrap_wear_authority(session,seeded_domain.tenant_a_id,a,qualified=True);await session.commit()
    assert (await bootstrap_wear_authority(session,seeded_domain.tenant_a_id,a,qualified=True)).owner=='legacy'
    authority=await session.get(WearBridgeAuthority,a);authority.owner='econtrol';await session.commit()
    settings.wear_bridge_scope={}
    with pytest.raises(HTTPException) as still_fenced:await lock_wear_writer(session,a)
    assert still_fenced.value.status_code==409
    assert await lock_wear_writer(session,b) is None
    await session.rollback()


async def test_disabled_restart_denied_after_cutover_and_recovers(session,seeded_domain,monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
    from apps.cloud_api.app import wear_bridge
    from apps.cloud_api.app.wear_bridge import verify_bridge_startup
    monkeypatch.setattr(wear_bridge,'_bridge_schema_present',False)
    factory=async_sessionmaker(session.bind,expire_on_commit=False)
    await verify_bridge_startup(factory,fence_enabled=False)
    installation=seeded_domain.installation_a_id
    session.add(WearBridgeAuthority(installation_id=installation,owner='econtrol',generation=1));await session.commit()
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=False,wear_bridge_scope={}))
    with pytest.raises(HTTPException) as fenced:await lock_wear_writer(session,installation)
    assert fenced.value.status_code==409
    await session.rollback()
    with pytest.raises(RuntimeError,match='disable_requires_rollback'):
        await verify_bridge_startup(factory,fence_enabled=False)
    await verify_bridge_startup(factory,fence_enabled=True)
    authority=await session.get(WearBridgeAuthority,installation);authority.owner='legacy';await session.commit()
    await verify_bridge_startup(factory,fence_enabled=False)
    empty=create_async_engine('sqlite+aiosqlite:///:memory:')
    try:
        empty_factory=async_sessionmaker(empty,expire_on_commit=False)
        await verify_bridge_startup(empty_factory,fence_enabled=False)
        with pytest.raises(RuntimeError,match='schema_missing'):await verify_bridge_startup(empty_factory,fence_enabled=True)
    finally:await empty.dispose()


async def test_scoped_bootstrap_concurrent_sqlite_keeps_one_row(tmp_path,monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from sqlalchemy import func
    from apps.cloud_api.app.database import Base
    from apps.cloud_api.app.domain.models import Installation
    from apps.cloud_api.app.domain.enums import InstallationStatus
    from apps.cloud_api.app.wear_bridge import bootstrap_wear_authority
    tenant,installation=uuid4(),uuid4()
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'race.db'))
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        db.add(Installation(id=installation,tenant_id=tenant,name='Synthetic',public_id='synthetic-race',status=InstallationStatus.ACTIVE));await db.commit()
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_scope={str(tenant):[str(installation)]}))
    ready=asyncio.Event()
    async def worker():
        async with factory() as db:
            await ready.wait()
            try:
                await bootstrap_wear_authority(db,tenant,installation,qualified=True);await db.commit();return 'committed'
            except Exception:
                await db.rollback();return 'rejected'
    tasks=[asyncio.create_task(worker()) for _ in range(2)];ready.set();result=await asyncio.gather(*tasks)
    assert 'committed' in result
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(WearBridgeAuthority))==1
        row=await db.get(WearBridgeAuthority,installation);assert (row.owner,row.generation)==('legacy',0)
    await engine.dispose()

async def test_authority_response_reloads_after_interleaved_commit(session,seeded_domain,monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    installation=seeded_domain.installation_a_id;key=b'k'*32
    session.add(WearBridgeAuthority(installation_id=installation,owner='econtrol',generation=1,import_revision='a'*64));await session.commit()
    original_commit=session.commit;other_factory=async_sessionmaker(session.bind,expire_on_commit=False)
    async def interleaved_commit():
        await original_commit()
        async with other_factory() as other:
            await other.execute(update(WearBridgeAuthority).where(WearBridgeAuthority.installation_id==installation).values(owner='legacy',generation=2))
            await other.commit()
    monkeypatch.setattr(session,'commit',interleaved_commit)
    async def override():yield session
    async def provider(value):return key if value==installation else None
    app.dependency_overrides[get_database_session]=override;app.state.wear_bridge_key_provider=provider
    monkeypatch.setattr('apps.cloud_api.app.wear_control_service.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_control_enabled=True))
    now=int(time.time());claims={'issuer':'econtrol','audience':'evoice-wear-service','installation':str(installation),'scope':'wear.control.authority',
        'method':'POST','path':'/service/wear/control/authority','issued':now,'expires':now+30,'nonce':'n'*32}
    envelope={'claims':claims,'body':{},'signature':hmac.new(key,canonical(claims,{}),hashlib.sha256).hexdigest()}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            response=await client.post('/service/wear/control/authority',json=envelope)
            assert response.status_code==200
            assert (response.json()['statement']['owner'],response.json()['statement']['generation'])==('legacy',2)
    finally:
        app.dependency_overrides.pop(get_database_session,None);app.state.wear_bridge_key_provider=None

async def test_legacy_process_detects_schema_created_after_start(tmp_path,monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from apps.cloud_api.app.database import Base
    from apps.cloud_api.app.domain.models import Installation
    from apps.cloud_api.app.domain.enums import InstallationStatus
    from apps.cloud_api.app import wear_bridge
    from apps.cloud_api.app.wear_bridge import verify_bridge_startup
    tenant,installation=uuid4(),uuid4();engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'late-schema.db'))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(lambda sync:WearBridgeAuthority.__table__.drop(sync))
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        db.add(Installation(id=installation,tenant_id=tenant,name='Synthetic',public_id='synthetic-late',status=InstallationStatus.ACTIVE));await db.commit()
    monkeypatch.setattr(wear_bridge,'_bridge_schema_present',False)
    monkeypatch.setattr(wear_bridge,'get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=False,wear_bridge_scope={}))
    await verify_bridge_startup(factory,fence_enabled=False)
    async with factory() as db:
        assert await lock_wear_writer(db,installation) is None;await db.commit()
    async with engine.begin() as conn:await conn.run_sync(lambda sync:WearBridgeAuthority.__table__.create(sync))
    async with factory() as db:
        db.add(WearBridgeAuthority(installation_id=installation,owner='econtrol',generation=1));await db.commit()
    async with factory() as db:
        with pytest.raises(HTTPException) as denied:await lock_wear_writer(db,installation)
        assert denied.value.status_code==409
    await engine.dispose()

async def test_capability_negotiation_and_mapping_reconciliation_real_asgi(session,seeded_domain,tmp_path,monkeypatch):
    from apps.cloud_api.app.domain.models import Installation
    from apps.cloud_api.app.entity_sync import EntitySyncService
    from control_client import ControlClient
    from fresh_authority import FreshAuthority
    from fresh_render_capability import FreshRenderCapability
    from authority_refresh import AuthorityRefresh
    from mapping_overlay import MappingOverlay
    from mapping_refresh import MappingRefresh
    from capability_refresh import CapabilityRefresh
    from wear_listener_runtime import StreamGate
    installation=seeded_domain.installation_a_id;key=b'k'*32
    entity=await session.get(Entity,seeded_domain.entity_a_id);instance=await session.get(Installation,installation)
    entity.ha_entity_id='sensor.old';entity.ha_registry_id='synthetic-registry';instance.sync_revision=0
    device=WearDevice(credential_hash='7'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=installation,display_name='Synthetic',capabilities_json=['notification.receive'],
        notification_preferences_json={'enabled':True,'categories':['system'],'minimum_severity':'info'})
    session.add(device);await session.flush();device_id=device.id
    source=str(entity.id)
    snapshot={'devices':[{'device_id':str(device_id),'user_id':str(seeded_domain.user_a_id),'name':'Watch',
        'notification_rules':[{'id':'r','entity_id':source,'enabled':True,'trigger':'any_change'}],'quick_actions':[]}],
        'entity_mapping':{source:{'entity_id':'sensor.old','registry_id':'synthetic-registry','display_name':'Vecchio'}}}
    revision=hashlib.sha256(import_canonical(snapshot)).hexdigest();now=int(time.time())
    session.add(WearBridgeAuthority(installation_id=installation,owner='econtrol',generation=1,import_revision=revision));await session.commit()
    producer=Store(tmp_path/'mapping.sqlite');producer.initialize_legacy(str(installation))
    claims={'issuer':'evoice','audience':'econtrol','scope':'wear.snapshot.full','installation':str(installation),'generation':0,
        'revision':revision,'issued':now,'expires':now+60}
    verified_import(producer,{'claims':claims,'snapshot':snapshot,'signature':signature(key,claims,snapshot)},key,installation=installation,generation=0)
    producer.import_snapshot(str(installation),revision,{});producer.transfer(str(installation),'legacy',0,'econtrol')
    gate=StreamGate(producer,str(installation),1)
    async def override():yield session
    async def provider(value):return key if value==installation else None
    app.dependency_overrides[get_database_session]=override;app.state.wear_bridge_key_provider=provider
    settings=SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_control_enabled=True,wear_render_v2_enabled=False,wear_bridge_scope={})
    monkeypatch.setattr('apps.cloud_api.app.wear_control_service.get_settings',lambda:settings)
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:settings)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            async def send(op,payload):
                response=await client.post('/service/wear/control/'+op,json=payload)
                if response.status_code!=200:raise RuntimeError('control_rejected')
                return response.json()
            control=ControlClient(str(installation),key,send)
            async def send_authority(payload):return await send('authority',payload)
            authority=FreshAuthority(key,str(installation),1,revision)
            refresh=AuthorityRefresh(authority,str(installation),key,send_authority);await refresh.refresh()
            capability=FreshRenderCapability(key,authority)
            nonce,reply=await control.call('capabilities',{},include_nonce=True)
            with pytest.raises(Blocked):capability.accept(reply,nonce)
            settings.wear_render_v2_enabled=True
            nonce,reply=await control.call('capabilities',{},include_nonce=True);capability.accept(reply,nonce)
            assert capability.valid()
            with pytest.raises(Blocked):capability.accept(reply,nonce)
            capability_refresh=CapabilityRefresh(capability,control);await capability_refresh.refresh()
            nonce,first=await control.call('mapping',{'generation':1,'revision':revision,'entity_ids':[source]},include_nonce=True)
            overlay=MappingOverlay(producer,key,authority);assert overlay.reconcile(first,nonce)
            mapping_refresh=MappingRefresh(overlay,control)
            assert await mapping_refresh.refresh() is False
            assert producer.db.execute('SELECT state FROM wear_stream_gate').fetchone()==('blocked',)
            assert gate.acknowledge_gap('a'*64,'b'*64,True,lambda c:True);gate.begin()
            active=VerifiedRules(producer,str(installation),1,revision,qualified=True)
            assert overlay.rules(active)[0]['rule']['entity_id']=='sensor.old'
            listener=WearEvents(producer,str(installation),1,lambda:overlay.rules(active),enabled=True,
                authority_guard=authority.valid,render_v2_enabled=True,render_capability=capability,mapping_guard=overlay)
            old={'entity_id':'sensor.old','state':'off'}
            new={'entity_id':'sensor.old','state':'on','last_updated':'2026-11-20T10:00:01+00:00','attributes':{},'context':{'id':'synthetic'}}
            event={'id':1,'type':'event','event':{'event_type':'state_changed','data':{'entity_id':'sensor.old','old_state':old,'new_state':new}}}
            listener.accept(event)
            await EntitySyncService(session,instance).apply_delta(1,[{'registry_id':'synthetic-registry','entity_id':'sensor.new','domain':'sensor','state':'on','available':True}])
            entity.voice_name='Nome corrente';await session.commit()
            async def send_notification(payload):
                response=await client.post('/service/wear/notifications',json=payload)
                if response.status_code==409:raise Blocked('receiver_conflict')
                assert response.status_code==200,response.text
                return response.json()
            with pytest.raises(Blocked,match='receiver_conflict'):await deliver_one(producer,str(installation),1,key,send_notification)
            assert producer.next_pending(str(installation),1) is not None
            assert producer.db.execute('SELECT state FROM wear_stream_gate').fetchone()==('blocked',)
            assert (await session.scalars(select(WearNotification).where(WearNotification.device_id==device_id))).all()==[]
            assert await mapping_refresh.refresh() is True
            nonce2,second=await control.call('mapping',{'generation':1,'revision':revision,'entity_ids':[source]},include_nonce=True)
            assert overlay.reconcile(second,nonce2) is False
            assert producer.db.execute('SELECT state FROM wear_stream_gate').fetchone()==('blocked',)
            assert overlay.rules(VerifiedRules(producer,str(installation),1,revision,qualified=True))[0]['rule']['entity_id']=='sensor.new'
            with pytest.raises(Blocked,match='mapping_stale'):overlay.reconcile(first,nonce)
            with pytest.raises(Blocked):gate.begin()
            assert gate.acknowledge_gap('c'*64,'d'*64,True,lambda c:True);gate.begin()
            class OfflineControl:
                async def call(self,*args,**kwargs):raise TimeoutError('synthetic outage')
            with pytest.raises(Blocked,match='mapping_refresh_failed'):await MappingRefresh(overlay,OfflineControl()).refresh()
            assert producer.db.execute('SELECT state FROM wear_stream_gate').fetchone()==('blocked',)
            with pytest.raises(Blocked,match='capability_refresh_failed'):await CapabilityRefresh(capability,OfflineControl()).refresh()
            assert not capability.valid()
            with pytest.raises(Blocked,match='render_capability_unverified'):listener.accept(event)
            foreign=str(seeded_domain.entity_b_id)
            with pytest.raises(Blocked):await control.call('mapping',{'generation':1,'revision':revision,'entity_ids':[foreign]})
    finally:
        app.dependency_overrides.pop(get_database_session,None);app.state.wear_bridge_key_provider=None;producer.close()
