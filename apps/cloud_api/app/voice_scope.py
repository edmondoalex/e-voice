"""Presentation-only view of the current Home Assistant voice selection.

Legacy control authorization and Alexa/Wear rules remain independent.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.sql.elements import ColumnElement

from .config import get_settings as get_settings
from .domain.models import Entity

VOICE_SELECTION_KEY = "_ekonex_voice_selected"


def selection_from_item(item: Mapping[str, object], previous: object) -> bool | None:
    selected = item.get("control_exposed")
    if type(selected) is bool:
        return selected
    return previous if type(previous) is bool else None


def voice_visible(entity: Entity) -> bool:
    selected = (entity.attributes_json or {}).get(VOICE_SELECTION_KEY)
    return selected if type(selected) is bool else entity.control_exposed is True


def voice_visible_clause(entity: type[Entity]) -> ColumnElement[bool]:
    selected = entity.attributes_json[VOICE_SELECTION_KEY].as_boolean()
    legacy = or_(selected.is_(True), and_(selected.is_(None), entity.control_exposed.is_(True)))
    settings = get_settings()
    if not (
        getattr(settings, "voice_selection_v1_enabled", False)
        and getattr(settings, "voice_selection_projection_enabled", False)
    ):
        return legacy
    from .voice_selection_store import VoiceSelectionMember

    current = selection_current_clause(entity.installation_id)
    member = exists(
        select(VoiceSelectionMember.installation_id).where(
            VoiceSelectionMember.installation_id == entity.installation_id,
            VoiceSelectionMember.registry_id == entity.ha_registry_id,
        )
    )
    return and_(current, member)


def selection_current_clause(installation_id: Any) -> ColumnElement[bool]:
    from .domain.models import ConnectorCredential
    from .voice_selection_store import VoiceSelectionFence, VoiceSelectionSnapshot

    current = exists(
        select(VoiceSelectionSnapshot.installation_id)
        .join(
            VoiceSelectionFence,
            VoiceSelectionFence.installation_id == VoiceSelectionSnapshot.installation_id,
        )
        .join(ConnectorCredential, ConnectorCredential.id == VoiceSelectionFence.credential_id)
        .where(
            VoiceSelectionSnapshot.installation_id == installation_id,
            VoiceSelectionSnapshot.tenant_id == VoiceSelectionFence.tenant_id,
            VoiceSelectionSnapshot.session_id == VoiceSelectionFence.session_id,
            VoiceSelectionFence.active.is_(True),
            VoiceSelectionSnapshot.expires_at > datetime.now(UTC),
            ConnectorCredential.installation_id == installation_id,
            ConnectorCredential.revoked_at.is_(None),
        )
    )
    return current
