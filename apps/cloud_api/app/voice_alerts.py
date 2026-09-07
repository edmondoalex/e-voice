"""One-shot Alexa alerts driven by synchronized Home Assistant states."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .database import async_session_factory
from .domain.models import AlexaVoiceAlert, Entity, Installation
from .laboratory_live_state import load_live_states, overlay_entities

logger = logging.getLogger(__name__)

STATE_WORDS = {
    "aperto": "open",
    "aperta": "open",
    "aperti": "open",
    "aperte": "open",
    "chiuso": "closed",
    "chiusa": "closed",
    "chiusi": "closed",
    "chiuse": "closed",
    "acceso": "on",
    "accesa": "on",
    "accesi": "on",
    "accese": "on",
    "spento": "off",
    "spenta": "off",
    "spenti": "off",
    "spente": "off",
    "bloccato": "locked",
    "bloccata": "locked",
    "sbloccato": "unlocked",
    "sbloccata": "unlocked",
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        re.findall(
            r"[a-z0-9]+",
            "".join(char for char in decomposed if not unicodedata.combining(char)),
        )
    )


def parse_alert_request(value: str) -> tuple[str, str] | None:
    normalized = _normalize(value)
    match = re.search(
        r"(?:quando|appena) (?:il |lo |la |i |gli |le )?(.+?) (?:e|diventa|risulta) "
        r"(apert[oaie]|chius[oaie]|acces[oaie]|spent[oaie]|bloccata|bloccato|sbloccata|sbloccato)$",
        normalized,
    )
    if match is None:
        return None
    return match.group(1).strip(), STATE_WORDS[match.group(2)]


def _entity_names(entity: Entity) -> tuple[str, ...]:
    aliases = tuple(item for item in entity.voice_aliases if isinstance(item, str))
    return tuple(
        item
        for item in (entity.voice_name, entity.display_name, entity.friendly_name, *aliases)
        if item
    )


def _matches_state(entity: Entity, expected: str) -> bool:
    state = (entity.state or "").casefold()
    if expected == "open":
        return state in {"on", "open", "opening"}
    if expected == "closed":
        return state in {"off", "closed", "closing"}
    return state == expected


async def _latest_echo_device_id(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    changed_after: datetime | None = None,
) -> str | None:
    """Return the last used Echo that also exposes an announcement entity."""
    speaker_devices = (
        select(Entity.device_id)
        .join(Installation, Installation.id == Entity.installation_id)
        .where(
            Installation.tenant_id == tenant_id,
            Entity.ha_domain == "notify",
            Entity.ha_entity_id.endswith("_annuncio"),
            Entity.device_id.is_not(None),
            Entity.deleted_at.is_(None),
        )
    )
    event_query = (
        select(Entity)
        .join(Installation, Installation.id == Entity.installation_id)
        .where(
            Installation.tenant_id == tenant_id,
            Entity.ha_domain == "event",
            Entity.device_id.in_(speaker_devices),
            Entity.available.is_(True),
            Entity.deleted_at.is_(None),
        )
    )
    if changed_after is not None:
        event_query = event_query.where(Entity.last_changed_at >= changed_after)
    latest_event = await session.scalar(
        event_query.order_by(Entity.last_changed_at.desc()).limit(1)
    )
    return latest_event.device_id if latest_event else None


async def create_voice_alert(
    session: AsyncSession,
    tenant_id: UUID,
    installation: Installation,
    request_text: str,
) -> tuple[bool, str]:
    parsed = parse_alert_request(request_text)
    if parsed is None:
        return False, "Prova, avvisami quando il cancello è chiuso."
    requested_name, expected = parsed
    entities = list(
        (
            await session.scalars(
                select(Entity).where(
                    Entity.installation_id == installation.id,
                    Entity.deleted_at.is_(None),
                    Entity.voice_name.is_not(None),
                )
            )
        ).all()
    )
    live_states = await load_live_states(installation.public_id)
    overlay_entities(entities, live_states)
    wanted = _normalize(requested_name)
    matches = [
        entity
        for entity in entities
        if any(
            wanted == _normalize(name)
            or len(wanted) >= 4
            and wanted in _normalize(name)
            for name in _entity_names(entity)
        )
    ]
    if len(matches) != 1:
        return False, (
            f"Ho trovato più dispositivi per {requested_name}. Usa un nome più preciso."
            if matches
            else f"Non trovo un dispositivo chiamato {requested_name}."
        )
    target = matches[0]
    spoken_name = target.voice_name or requested_name
    if _matches_state(target, expected):
        return False, f"{spoken_name} è già nello stato richiesto."

    now = datetime.now(UTC)
    source_device_id = await _latest_echo_device_id(
        session, tenant_id, changed_after=now - timedelta(seconds=5)
    )
    expected_it = {
        "open": "aperto",
        "closed": "chiuso",
        "on": "acceso",
        "off": "spento",
        "locked": "bloccato",
        "unlocked": "sbloccato",
    }[expected]
    alert = AlexaVoiceAlert(
        tenant_id=tenant_id,
        installation_id=installation.id,
        target_entity_id=target.id,
        expected_state=expected,
        source_device_id=source_device_id,
        message=f"{spoken_name} adesso è {expected_it}.",
        status="pending",
        expires_at=now + timedelta(hours=24),
    )
    session.add(alert)
    await session.commit()
    return True, f"Va bene. Ti avviserò quando {spoken_name} sarà {expected_it}."


def schedule_alert_evaluation(installation_id: UUID, changed_ids: list[UUID]) -> None:
    task = asyncio.create_task(_evaluate_after_sync(installation_id, changed_ids))
    task.add_done_callback(_log_task_error)


def _log_task_error(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("voice_alert_evaluation_failed", exc_info=task.exception())


async def _evaluate_after_sync(installation_id: UUID, changed_ids: list[UUID]) -> None:
    # Let the EVCP receive loop finish acknowledging the state update before a command is sent.
    await asyncio.sleep(0.5)
    async with async_session_factory() as session:
        await evaluate_voice_alerts(session, installation_id, changed_ids)


async def evaluate_voice_alerts(
    session: AsyncSession,
    installation_id: UUID,
    changed_ids: list[UUID],
    *,
    use_live_states: bool = False,
) -> None:
    now = datetime.now(UTC)
    installation = await session.get(Installation, installation_id)
    if installation is None:
        return
    changed = list(
        (
            await session.scalars(
                select(Entity).where(
                    Entity.installation_id == installation_id,
                    Entity.id.in_(changed_ids),
                )
            )
        ).all()
    )
    voice_events = [item for item in changed if item.ha_domain == "event" and item.device_id]
    source_device_id = None
    if voice_events:
        source_device_id = max(
            voice_events,
            key=lambda item: item.last_changed_at or datetime.min.replace(tzinfo=UTC),
        ).device_id
    if source_device_id is None:
        source_device_id = await _latest_echo_device_id(session, installation.tenant_id)
    if source_device_id is not None:
        binding_filters = [
            AlexaVoiceAlert.tenant_id == installation.tenant_id,
            AlexaVoiceAlert.status == "pending",
            AlexaVoiceAlert.source_device_id.is_(None),
        ]
        if voice_events:
            binding_filters.append(
                AlexaVoiceAlert.created_at >= now - timedelta(seconds=30)
            )
        else:
            # Give Alexa Devices time to publish the exact originating Echo first.
            binding_filters.append(
                AlexaVoiceAlert.created_at <= now - timedelta(seconds=10)
            )
        await session.execute(
            update(AlexaVoiceAlert)
            .where(*binding_filters)
            .values(source_device_id=source_device_id)
            .execution_options(synchronize_session=False)
        )
        await session.commit()

    alert_filters = [
        AlexaVoiceAlert.installation_id == installation_id,
        AlexaVoiceAlert.status == "pending",
        AlexaVoiceAlert.source_device_id.is_not(None),
        AlexaVoiceAlert.expires_at > now,
    ]
    if not voice_events:
        alert_filters.append(AlexaVoiceAlert.target_entity_id.in_(changed_ids))
    alerts = list(
        (
            await session.scalars(
                select(AlexaVoiceAlert).where(*alert_filters)
            )
        ).all()
    )
    live_states = await load_live_states(installation.public_id) if use_live_states else {}
    from .alexa_routines import _dispatch_announcement

    for alert in alerts:
        target = await session.get(Entity, alert.target_entity_id)
        if target is not None:
            overlay_entities((target,), live_states)
        if target is None or not _matches_state(target, alert.expected_state):
            continue
        speaker = await session.scalar(
            select(Entity)
            .join(Installation, Installation.id == Entity.installation_id)
            .where(
                Installation.tenant_id == alert.tenant_id,
                Entity.device_id == alert.source_device_id,
                Entity.ha_domain == "notify",
                Entity.ha_entity_id.endswith("_annuncio"),
                Entity.deleted_at.is_(None),
            )
        )
        if speaker is None:
            continue
        speaker_installation = await session.get(Installation, speaker.installation_id)
        if speaker_installation is None:
            continue
        alert.status = "dispatching"
        await session.commit()
        try:
            outcomes = await _dispatch_announcement(
                session,
                alert.tenant_id,
                speaker_installation,
                f"entity:{speaker.id}",
                "announce",
                alert.message,
            )
            if any(item.status == "success" for item in outcomes):
                alert.status = "triggered"
            elif any(
                item.error_code == "INSTALLATION_OFFLINE"
                or item.status == "unavailable"
                for item in outcomes
            ):
                alert.status = "pending"
            else:
                alert.status = "failed"
        except Exception:
            alert.status = "failed"
            logger.exception("voice_alert_dispatch_failed alert_id=%s", alert.id)
        alert.triggered_at = datetime.now(UTC) if alert.status != "pending" else None
        await session.commit()


async def run_live_alert_monitor() -> None:
    """Poll the read-only live overlay because lab exposure may contain only Echo devices."""
    while True:
        try:
            async with async_session_factory() as session:
                pending = list(
                    (
                        await session.scalars(
                            select(AlexaVoiceAlert).where(
                                AlexaVoiceAlert.status == "pending",
                                AlexaVoiceAlert.expires_at > datetime.now(UTC),
                            )
                        )
                    ).all()
                )
                by_installation: dict[UUID, list[UUID]] = {}
                for alert in pending:
                    by_installation.setdefault(alert.installation_id, []).append(
                        alert.target_entity_id
                    )
                for installation_id, target_ids in by_installation.items():
                    await evaluate_voice_alerts(
                        session,
                        installation_id,
                        list(dict.fromkeys(target_ids)),
                        use_live_states=True,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("voice_alert_monitor_failed")
        await asyncio.sleep(2)
