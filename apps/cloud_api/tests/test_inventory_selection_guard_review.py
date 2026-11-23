from uuid import uuid4

import pytest

from apps.cloud_api.app.domain.models import Installation
from apps.cloud_api.app.voice_selection_store import (
    VoiceSelectionSnapshot,
    persist_selection,
    register_selection_session,
)


async def test_selection_requires_committed_inventory_and_refresh(session, seeded_domain):
    i = seeded_domain.installation_a_id
    t = seeded_domain.tenant_a_id
    sid = uuid4()
    obj = await session.get(Installation, i)
    obj.sync_revision = 4
    await register_selection_session(session, i, t, sid)
    await session.commit()
    with pytest.raises(ValueError, match="revision_mismatch"):
        await persist_selection(session, i, t, sid, 5, ["new"], require_inventory_revision=True)
    await session.rollback()
    assert await session.get(VoiceSelectionSnapshot, i) is None
    await persist_selection(session, i, t, sid, 4, ["good"], require_inventory_revision=True)
    await session.commit()
    await persist_selection(session, i, t, sid, 4, ["good"], require_inventory_revision=True)
    await session.commit()
    row = await session.get(VoiceSelectionSnapshot, i)
    assert row.registry_ids == ["good"] and row.revision == 4
    with pytest.raises(ValueError, match="revision_mismatch"):
        await persist_selection(session, i, t, sid, 5, ["new"], require_inventory_revision=True)
    await session.rollback()
