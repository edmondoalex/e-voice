import pytest
from uuid import uuid4
from apps.cloud_api.app.voice_selection_store import VoiceSelectionSnapshot,persist_selection,register_selection_session
from apps.cloud_api.app.evcp import VoiceSelectionPayload

def test_selection_revision_cannot_overflow_bigint():
    valid={'session_id':uuid4(),'revision':2**63-1,'batch_index':0,'batch_count':1,'registry_ids':[]}
    assert VoiceSelectionPayload.model_validate(valid).revision==2**63-1
    with pytest.raises(ValueError):VoiceSelectionPayload.model_validate({**valid,'revision':2**63})

async def test_scope_revision_and_duplicates_fail_closed(session,seeded_domain):
    installation=seeded_domain.installation_a_id;tenant=seeded_domain.tenant_a_id
    active=uuid4();await register_selection_session(session,installation,tenant,active);await session.commit()
    with pytest.raises(ValueError,match='scope_invalid'):
        await persist_selection(session,installation,seeded_domain.tenant_b_id,active,1,['bad'])
    await session.rollback()
    with pytest.raises(ValueError,match='duplicate'):
        await persist_selection(session,installation,tenant,active,1,['a','a'])
    await session.rollback()
    await persist_selection(session,installation,tenant,active,2,['b','a']);await session.commit()
    await persist_selection(session,installation,tenant,active,2,['a','b']);await session.commit()
    for revision,ids in ((1,['a','b']),(2,['a'])):
        with pytest.raises(ValueError,match='stale_or_conflict'):
            await persist_selection(session,installation,tenant,active,revision,ids)
        await session.rollback()
    row=await session.get(VoiceSelectionSnapshot,installation)
    assert row.registry_ids==['a','b'] and row.revision==2
    replacement=uuid4();await register_selection_session(session,installation,tenant,replacement);await session.commit()
    with pytest.raises(ValueError,match='session_replaced'):
        await persist_selection(session,installation,tenant,active,3,['stale'])
    await session.rollback()
    await persist_selection(session,installation,tenant,replacement,3,['fresh']);await session.commit()
    row=await session.get(VoiceSelectionSnapshot,installation,populate_existing=True)
    assert row.registry_ids==['fresh']
