from types import SimpleNamespace
import copy
import pytest
from fastapi import Response
from sqlalchemy import select

from apps.cloud_api.app.domain.models import Entity, WearDevice
from apps.cloud_api.app.wear_api import export_wear_selection
from migration import MigrationBlocked, plan


async def test_export_scoped_and_no_owner_change(session,seeded_domain):
    entity=(await session.scalars(select(Entity).where(Entity.installation_id==seeded_domain.installation_a_id))).first()
    foreign=(await session.scalars(select(Entity).where(Entity.installation_id==seeded_domain.installation_b_id))).first()
    assert entity is not None and foreign is not None
    entity.ha_registry_id='synthetic-registry'
    preferences={'notification_rules':[{'id':'r','entity_id':str(entity.id),'trigger':'any_change'}, {'id':'foreign','entity_id':str(foreign.id),'trigger':'any_change'}],
                 'quick_actions':[{'slot':1,'entity_id':str(entity.id),'operation':'power_on'}], 'secret_test':'never-export'}
    device=WearDevice(credential_hash='a'*64,tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        installation_id=seeded_domain.installation_a_id,display_name='Synthetic',capabilities_json=[],notification_preferences_json=preferences)
    session.add(device);await session.commit()
    context=SimpleNamespace(tenant_id=seeded_domain.tenant_a_id,user_id=seeded_domain.user_a_id,
        effective_installation_id=seeded_domain.installation_a_id,require=lambda capability:None)
    response=Response(); first=await export_wear_selection(response,context,session)
    assert response.headers['cache-control']=='no-store'
    assert first['active_owner_unchanged'] is True
    assert len(first['snapshot']['devices'])==1
    assert str(entity.id) in first['snapshot']['entity_mapping']
    assert str(foreign.id) not in first['snapshot']['entity_mapping']
    assert 'never-export' not in repr(first)
    assert device.notification_preferences_json==preferences
    assert (await export_wear_selection(Response(),context,session))['revision']==first['revision']
    with pytest.raises(MigrationBlocked,match='unmapped_entity'):
        plan(first['snapshot'],first['snapshot']['entity_mapping'])
    safe=copy.deepcopy(first['snapshot'])
    safe['devices'][0]['notification_rules']=safe['devices'][0]['notification_rules'][:1]
    # Synthetic input needs a valid rule trigger, as required by the migration contract.
    safe['devices'][0]['notification_rules'][0]['trigger']='any_change'
    converted=plan(safe,safe['entity_mapping'])
    assert converted['devices'][0]['notification_rules'][0]['registry_id']=='synthetic-registry'
    assert converted['devices'][0]['quick_actions'][0]['registry_id']=='synthetic-registry'
    other=SimpleNamespace(tenant_id=seeded_domain.tenant_b_id,user_id=seeded_domain.user_b_id,
        effective_installation_id=seeded_domain.installation_b_id,require=lambda capability:None)
    assert (await export_wear_selection(Response(),other,session))['snapshot']['devices']==[]
