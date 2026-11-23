import asyncio,hashlib
from types import SimpleNamespace
import pytest
from apps.cloud_api.app import evcp
from apps.cloud_api.app.domain.enums import InstallationStatus
from apps.cloud_api.app.domain.models import Installation,ConnectorCredential
from apps.cloud_api.app.voice_selection_store import VoiceSelectionSnapshot
from test_evcp import FakeConnectorWebSocket,message,sessions,connector_websocket,CONNECTOR_CAPABILITIES

@pytest.mark.parametrize('flag,cap,full,should_negotiate,should_store',[(False,False,False,False,True),(False,True,False,False,True),(True,False,False,False,True),(True,True,False,True,False),(True,True,'partial',True,False),(True,True,True,True,True)])
async def test_mixed_peer_negotiation(session,seeded_domain,monkeypatch,flag,cap,full,should_negotiate,should_store):
 i=seeded_domain.installation_a_id;obj=await session.get(Installation,i);obj.status=InstallationStatus.ACTIVE;secret='evc_synthetic_mixed_peer';session.add(ConnectorCredential(installation_id=i,secret_hash=hashlib.sha256(secret.encode()).hexdigest()));await session.commit()
 monkeypatch.setattr(evcp,'get_settings',lambda:SimpleNamespace(voice_selection_v1_enabled=True,voice_selection_inventory_fence_enabled=flag))
 ws=FakeConnectorWebSocket(secret)
 if i in sessions._sessions:await sessions.remove(i,sessions._sessions[i].session_id)
 task=asyncio.create_task(connector_websocket(ws,session))
 try:
  await ws.inbound.put(message('hello',{'installation_id':str(i),'connector_version':'0.1.8-beta.5','ha_version':'2026.8.0','protocol_versions':[1],'capabilities':{**CONNECTOR_CAPABILITIES,'supports_voice_selection_v1':True,'supports_voice_selection_inventory_fence_v1':cap}}))
  ack=await asyncio.wait_for(ws.outbound.get(),2);assert (ack['payload'].get('voice_selection_inventory_fence_v1') is True)==should_negotiate;sid=ack['payload']['session_id']
  if full:await ws.inbound.put(message('inventory_full',{'session_id':sid,'revision':1,'batch_index':0,'batch_count':2 if full=='partial' else 1,'entities':[]}))
  await ws.inbound.put(message('voice_selection_v1',{'session_id':sid,'revision':1,'batch_index':0,'batch_count':1,'registry_ids':['selected']}))
  if should_store:
   await ws.inbound.put(message('heartbeat',{'session_id':sid}));assert (await asyncio.wait_for(ws.outbound.get(),2))['type']=='heartbeat_ack'
   assert (await session.get(VoiceSelectionSnapshot,i)).registry_ids==['selected']
  else:
   await asyncio.wait_for(task,2);assert ws.close_code==4002;assert await session.get(VoiceSelectionSnapshot,i) is None
 finally:
  if not task.done():
   task.cancel()
   with pytest.raises(asyncio.CancelledError):await task
