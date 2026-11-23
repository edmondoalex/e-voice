from types import SimpleNamespace
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.cloud_api.app import wear_api
from apps.cloud_api.app.domain.models import WearDevice
from apps.cloud_api.app.wear_bridge import WearBridgeAuthority


@pytest.mark.parametrize('operation', ['preferences', 'actions', 'rules'])
async def test_preferences_do_not_erase_other_session_changes(session, seeded_domain, monkeypatch, operation):
    installation=seeded_domain.installation_a_id
    device=WearDevice(credential_hash='f'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=installation,display_name='Synthetic',capabilities_json=[],
        notification_preferences_json={'enabled':False,'notification_rules':[],'quick_actions':[]})
    session.add_all([device,WearBridgeAuthority(installation_id=installation,owner='legacy',generation=0)])
    await session.commit();device_id=device.id
    factory=async_sessionmaker(session.bind,expire_on_commit=False)
    async with factory() as writer:
        remote=await writer.get(WearDevice,device_id)
        remote.notification_preferences_json={'enabled':True,'notification_rules':[{'id':'remote-rule'}],
                                              'quick_actions':[{'slot':1,'label':'remote'}]}
        await writer.commit()
    assert device.notification_preferences_json['notification_rules']==[]
    monkeypatch.setattr(wear_api,'_ui_csrf',lambda context:'ok')
    monkeypatch.setattr('apps.cloud_api.app.wear_bridge.get_settings',lambda:SimpleNamespace(wear_bridge_fence_enabled=True,wear_bridge_scope={}))
    context=SimpleNamespace(require=lambda _:None,effective_installation_id=installation,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id)
    if operation=='preferences':
        payload=wear_api.NotificationPreferencesRequest(enabled=False,categories={'system'},minimum_severity='info',vibration=True)
        await wear_api.update_notification_preferences(device_id,payload,context,session,'ok')
        assert device.notification_preferences_json['notification_rules']==[{'id':'remote-rule'}]
        assert device.notification_preferences_json['quick_actions']==[{'slot':1,'label':'remote'}]
    elif operation=='actions':
        await wear_api.update_quick_actions(device_id,wear_api.QuickActionsRequest(actions=[]),context,session,'ok')
        assert device.notification_preferences_json['notification_rules']==[{'id':'remote-rule'}]
        assert device.notification_preferences_json['enabled'] is True
    else:
        await wear_api.update_notification_rules(device_id,wear_api.NotificationRulesRequest(rules=[]),context,session,'ok')
        assert device.notification_preferences_json['quick_actions']==[{'slot':1,'label':'remote'}]
        assert device.notification_preferences_json['enabled'] is True
