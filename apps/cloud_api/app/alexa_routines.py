"""Laboratory portal for Alexa Devices speakers, groups and manual announcements."""

# HTML is intentionally inline for this isolated server-rendered laboratory page.
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, time
from itertools import count
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .admin_console import (
    _admin,
    _e,
    _installation,
    _layout,
    console_context_dependency,
)
from .auth import TenantContext
from .command_dispatch import CommandDispatchService, DispatchOutcome, command_adapter
from .config import get_settings
from .database import get_database_session
from .domain.models import (
    AlexaRoutineExecution,
    AlexaSpeakerGroup,
    AlexaSpeakerGroupMember,
    AlexaVoiceAlert,
    AlexaVoiceRoutine,
    ConnectorCredential,
    Entity,
    Installation,
)
from .evcp import _authenticate_secret as authenticate_connector_secret
from .evcp import sessions
from .pairing_api import CSRF_COOKIE, _csrf, _valid_csrf
from .voice_categories import category_slug

router = APIRouter(prefix="/alexa-routines", tags=["alexa-routines"])
connector_router = APIRouter(prefix="/connector/v1/routines", tags=["connector-routines"])
session_dependency = Depends(get_database_session)
MAX_FORM_BYTES = 64_000
ANNOUNCE_SUFFIXES = ("_announce", "_annuncio")
SPEAK_SUFFIXES = ("_speak", "_parla")
ROME = ZoneInfo("Europe/Rome")
VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-z_]+\.[a-z0-9_]+)\s*\}\}", re.IGNORECASE)
_queue_sequence = count()
_announcement_queues: dict[
    UUID,
    asyncio.PriorityQueue[
        tuple[
            int,
            int,
            asyncio.Future[list[DispatchOutcome]],
            Callable[[], Awaitable[list[DispatchOutcome]]],
        ]
    ],
] = {}
_announcement_workers: dict[UUID, asyncio.Task[None]] = {}


class RoutineTriggerRequest(BaseModel):
    """Bounded message rendered by Home Assistant before transport."""

    model_config = ConfigDict(extra="forbid", strict=True)
    message: str | None = Field(default=None, max_length=500)


class RoutineTriggerResponse(BaseModel):
    routine: str
    attempted: int
    succeeded: int
    status: Literal["success", "partial", "failed"]


def _clock(value: str | None) -> time | None:
    if not value:
        return None
    return time.fromisoformat(value)


def _within_period(now: time, start: time, end: time) -> bool:
    return start <= now < end if start < end else now >= start or now < end


async def _render_variables(session: AsyncSession, tenant_id: UUID, message: str) -> str:
    requested = set(VARIABLE_PATTERN.findall(message))
    if not requested:
        return message
    entities = list(
        (
            await session.scalars(
                select(Entity)
                .join(Installation, Installation.id == Entity.installation_id)
                .where(
                    Installation.tenant_id == tenant_id,
                    Entity.ha_entity_id.in_(requested),
                    Entity.deleted_at.is_(None),
                )
            )
        ).all()
    )
    values = {item.ha_entity_id.casefold(): item.state or "non disponibile" for item in entities}
    return VARIABLE_PATTERN.sub(
        lambda match: values.get(match.group(1).casefold(), "non disponibile"), message
    )


async def _routine_allowed(session: AsyncSession, routine: AlexaVoiceRoutine) -> bool:
    now = datetime.now(ROME).time()
    start, end = _clock(routine.condition_start), _clock(routine.condition_end)
    if start is not None and end is not None and not _within_period(now, start, end):
        return False
    if routine.condition_entity_id is not None:
        entity = await session.get(Entity, routine.condition_entity_id)
        if entity is None or not entity.available:
            return False
        if (
            routine.condition_state
            and (entity.state or "").casefold() != routine.condition_state.casefold()
        ):
            return False
    return True


def _routine_volume(routine: AlexaVoiceRoutine) -> int | None:
    start, end = _clock(routine.night_start), _clock(routine.night_end)
    if (
        routine.night_volume_percent is not None
        and start is not None
        and end is not None
        and _within_period(datetime.now(ROME).time(), start, end)
    ):
        return routine.night_volume_percent
    return routine.volume_percent


def _advanced_options(values: dict[str, list[str]]) -> dict[str, object]:
    def optional_int(name: str) -> int | None:
        raw = _one(values, name)
        return int(raw) if raw else None

    night_volume = optional_int("night_volume_percent")
    repeat_count = optional_int("repeat_count") or 1
    repeat_interval = optional_int("repeat_interval_seconds") or 1
    priority = optional_int("priority") or 5
    sound = _one(values, "sound") or "default"
    night_start, night_end = _one(values, "night_start") or None, _one(values, "night_end") or None
    condition_start = _one(values, "condition_start") or None
    condition_end = _one(values, "condition_end") or None
    if sound not in {"default", "none", "bell", "warning", "emergency"}:
        raise ValueError
    if not 1 <= repeat_count <= 3 or not 1 <= repeat_interval <= 30 or not 1 <= priority <= 10:
        raise ValueError
    if night_volume is not None and not 0 <= night_volume <= 100:
        raise ValueError
    for clock_value in (night_start, night_end, condition_start, condition_end):
        _clock(clock_value)
    return {
        "night_volume_percent": night_volume,
        "night_start": night_start,
        "night_end": night_end,
        "restore_volume": _one(values, "restore_volume") == "on",
        "sound": sound,
        "repeat_count": repeat_count,
        "repeat_interval_seconds": repeat_interval,
        "priority": priority,
        "condition_entity_id": UUID(_one(values, "condition_entity_id"))
        if _one(values, "condition_entity_id")
        else None,
        "condition_state": _one(values, "condition_state") or None,
        "condition_start": condition_start,
        "condition_end": condition_end,
    }


async def _condition_belongs_to_tenant(
    session: AsyncSession, tenant_id: UUID, entity_id: object
) -> bool:
    if entity_id is None:
        return True
    return (
        await session.scalar(
            select(Entity.id)
            .join(Installation, Installation.id == Entity.installation_id)
            .where(Entity.id == entity_id, Installation.tenant_id == tenant_id)
        )
        is not None
    )


def _laboratory_only() -> None:
    if get_settings().environment != "laboratory":
        raise HTTPException(status.HTTP_404_NOT_FOUND)


async def _multi_form(request: Request) -> dict[str, list[str]]:
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        return parse_qs(raw.decode(), strict_parsing=True)
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Dati non validi") from error


def _one(values: dict[str, list[str]], key: str) -> str:
    items = values.get(key, [])
    return items[0].strip() if items else ""


def _portal_redirect(
    installation_id: UUID,
    *,
    notice: str | None = None,
    message: str | None = None,
) -> RedirectResponse:
    params = {"installation": str(installation_id)}
    if notice:
        params["notice"] = notice
    if message:
        params["message"] = message
    return RedirectResponse(
        f"/alexa-routines?{urlencode(params)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _installations(session: AsyncSession, context: TenantContext) -> list[Installation]:
    return list(
        (
            await session.scalars(
                select(Installation)
                .where(Installation.tenant_id == context.tenant_id)
                .order_by(Installation.name)
            )
        ).all()
    )


async def _speakers(session: AsyncSession, installation_id: UUID) -> list[Entity]:
    """Use Announce as the canonical per-device membership entity."""
    return list(
        (
            await session.scalars(
                select(Entity)
                .where(
                    Entity.installation_id == installation_id,
                    Entity.ha_domain == "notify",
                    or_(*(Entity.ha_entity_id.endswith(suffix) for suffix in ANNOUNCE_SUFFIXES)),
                    Entity.deleted_at.is_(None),
                )
                .order_by(Entity.display_name, Entity.friendly_name, Entity.ha_entity_id)
            )
        ).all()
    )


async def _groups(session: AsyncSession, installation_id: UUID) -> list[AlexaSpeakerGroup]:
    return list(
        (
            await session.scalars(
                select(AlexaSpeakerGroup)
                .options(selectinload(AlexaSpeakerGroup.members))
                .where(AlexaSpeakerGroup.installation_id == installation_id)
                .order_by(AlexaSpeakerGroup.name)
            )
        ).all()
    )


async def _routines(session: AsyncSession, installation_id: UUID) -> list[AlexaVoiceRoutine]:
    return list(
        (
            await session.scalars(
                select(AlexaVoiceRoutine)
                .where(AlexaVoiceRoutine.installation_id == installation_id)
                .order_by(AlexaVoiceRoutine.name)
            )
        ).all()
    )


async def _alerts(session: AsyncSession, installation_id: UUID) -> list[AlexaVoiceAlert]:
    return list(
        (
            await session.scalars(
                select(AlexaVoiceAlert)
                .options(selectinload(AlexaVoiceAlert.target_entity))
                .where(AlexaVoiceAlert.installation_id == installation_id)
                .order_by(AlexaVoiceAlert.created_at.desc())
                .limit(50)
            )
        ).all()
    )


async def _executions(session: AsyncSession, installation_id: UUID) -> list[AlexaRoutineExecution]:
    return list(
        (
            await session.scalars(
                select(AlexaRoutineExecution)
                .where(AlexaRoutineExecution.installation_id == installation_id)
                .order_by(AlexaRoutineExecution.created_at.desc())
                .limit(50)
            )
        ).all()
    )


def _speaker_name(entity: Entity) -> str:
    return entity.display_name or entity.friendly_name or entity.ha_entity_id


def _updated_group_members(
    group: AlexaSpeakerGroup, selected: dict[UUID, Entity]
) -> list[AlexaSpeakerGroupMember]:
    """Keep existing membership rows so an edit cannot violate their unique key."""
    existing = {member.entity_id: member for member in group.members}
    return [
        existing.get(entity_id) or AlexaSpeakerGroupMember(entity_id=entity_id)
        for entity_id in selected
    ]


@router.get("", response_class=HTMLResponse)
async def routines_page(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _laboratory_only()
    _admin(context)
    installations = await _installations(session, context)
    requested = request.query_params.get("installation", "")
    installation = next((item for item in installations if str(item.id) == requested), None)
    if installation is None and installations:
        installation = installations[0]
    csrf = _csrf(context)
    if installation is None:
        body = '<div class="card">Nessun impianto disponibile.</div>'
    else:
        speakers = await _speakers(session, installation.id)
        groups = await _groups(session, installation.id)
        routines = await _routines(session, installation.id)
        alerts = await _alerts(session, installation.id)
        executions = await _executions(session, installation.id)
        condition_entities = list(
            (
                await session.scalars(
                    select(Entity)
                    .join(Installation, Installation.id == Entity.installation_id)
                    .where(
                        Installation.tenant_id == context.tenant_id,
                        Entity.deleted_at.is_(None),
                        Entity.ha_domain.not_in(("notify", "event")),
                    )
                    .order_by(Entity.voice_name, Entity.friendly_name)
                    .limit(500)
                )
            ).all()
        )
        speaker_by_id = {item.id: item for item in speakers}
        installation_options = "".join(
            f'<option value="{item.id}"{" selected" if item.id == installation.id else ""}>{_e(item.name)}</option>'
            for item in installations
        )
        speaker_options = "".join(
            f'<label style="display:block;margin:8px 0"><input type="checkbox" name="member" value="{item.id}"> {_e(_speaker_name(item))}</label>'
            for item in speakers
        )

        def destination_options_for(selected: str | None = None) -> str:
            choices = [
                ("all", "Ovunque (automatico)"),
                ("last", "Ultimo Echo utilizzato (automatico)"),
            ]
            choices.extend((f"entity:{item.id}", _speaker_name(item)) for item in speakers)
            choices.extend((f"group:{group.id}", f"Gruppo: {group.name}") for group in groups)
            return "".join(
                f'<option value="{value}"{" selected" if value == selected else ""}>{_e(label)}</option>'
                for value, label in choices
            )

        destination_options = destination_options_for()

        def advanced_fields(item: AlexaVoiceRoutine | None = None) -> str:
            selected_condition = item.condition_entity_id if item else None
            condition_options = '<option value="">Nessuna</option>' + "".join(
                f'<option value="{entity.id}"{" selected" if entity.id == selected_condition else ""}>{_e(entity.voice_name or entity.friendly_name or entity.ha_entity_id)}</option>'
                for entity in condition_entities
            )
            selected_sound = item.sound if item else "default"
            sounds = "".join(
                f'<option value="{value}"{" selected" if value == selected_sound else ""}>{label}</option>'
                for value, label in (
                    ("default", "Annuncio standard"),
                    ("none", "Nessun suono"),
                    ("bell", "Campanello"),
                    ("warning", "Avviso"),
                    ("emergency", "Emergenza"),
                )
            )

            def value(name: str, default: object = "") -> str:
                current = getattr(item, name, default) if item else default
                return _e(str(current if current is not None else default))

            checked = " checked" if item and item.restore_volume else ""
            return f'''<details><summary>Opzioni avanzate</summary>
<label class="field"><b>Volume notte (%)</b><input name="night_volume_percent" type="number" min="0" max="100" value="{value("night_volume_percent")}"></label>
<label class="field"><b>Fascia notte</b><input name="night_start" type="time" value="{value("night_start", "22:00")}"> — <input name="night_end" type="time" value="{value("night_end", "07:00")}"></label>
<label><input name="restore_volume" type="checkbox"{checked}> Ripristina il volume precedente</label>
<label class="field"><b>Suono</b><select name="sound">{sounds}</select></label>
<label class="field"><b>Ripetizioni</b><input name="repeat_count" type="number" min="1" max="3" value="{value("repeat_count", 1)}"></label>
<label class="field"><b>Intervallo ripetizioni (secondi)</b><input name="repeat_interval_seconds" type="number" min="1" max="30" value="{value("repeat_interval_seconds", 1)}"></label>
<label class="field"><b>Priorità (1 alta, 10 bassa)</b><input name="priority" type="number" min="1" max="10" value="{value("priority", 5)}"></label>
<label class="field"><b>Condizione entità</b><select name="condition_entity_id">{condition_options}</select></label>
<label class="field"><b>Stato richiesto</b><input name="condition_state" value="{value("condition_state")}" placeholder="es. on, off, home"></label>
<label class="field"><b>Fascia consentita</b><input name="condition_start" type="time" value="{value("condition_start")}"> — <input name="condition_end" type="time" value="{value("condition_end")}"></label>
</details>'''

        group_rows = "".join(
            "<tr>"
            f"<td><b>{_e(group.name)}</b><br><span class=muted>{_e(group.slug)}</span></td>"
            f"<td>{', '.join(_e(_speaker_name(speaker_by_id[member.entity_id])) for member in group.members if member.entity_id in speaker_by_id) or 'Nessun Echo'}</td>"
            f'<td><details><summary>Modifica</summary><form method="post" action="/alexa-routines/groups/{group.id}"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}"><label class="field"><b>Nome</b><input name="name" maxlength="120" value="{_e(group.name)}" required></label>'
            + "".join(
                f'<label style="display:block;margin:8px 0"><input type="checkbox" name="member" value="{speaker.id}"{" checked" if any(member.entity_id == speaker.id for member in group.members) else ""}> {_e(_speaker_name(speaker))}</label>'
                for speaker in speakers
            )
            + f'<button>Salva gruppo</button></form></details><form method="post" action="/alexa-routines/groups/{group.id}/delete"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}"><button class="danger">Elimina</button></form></td>'
            "</tr>"
            for group in groups
        )
        destination_names = {
            "all:": "Ovunque",
            "last:": "Ultimo Echo utilizzato",
            **{f"entity:{item.id}": _speaker_name(item) for item in speakers},
            **{f"group:{item.id}": f"Gruppo: {item.name}" for item in groups},
        }
        routine_rows = "".join(
            "<tr>"
            f"<td><b>{_e(item.name)}</b><br><span class=muted>{_e(item.slug)}</span></td>"
            f"<td>{_e(destination_names.get(f'{item.destination_type}:{item.destination_id or ""}', 'Destinazione non disponibile'))}</td>"
            f"<td>{_e(item.mode)}<br><span class=muted>Volume: {_e(str(item.volume_percent) + '%' if item.volume_percent is not None else 'invariato')}</span></td><td>{_e(item.default_message or 'Messaggio fornito dal trigger Home Assistant')}</td>"
            f'<td><details><summary>Modifica</summary><form method="post" action="/alexa-routines/routines/{item.id}"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}"><label class="field"><b>Nome</b><input name="name" maxlength="120" value="{_e(item.name)}" required></label><label class="field"><b>Destinatario</b><select name="destination">{destination_options_for(_routine_destination(item))}</select></label><label class="field"><b>Modalità</b><select name="mode"><option value="announce"{" selected" if item.mode == "announce" else ""}>Annuncio</option><option value="speak"{" selected" if item.mode == "speak" else ""}>Parla</option></select></label><label class="field"><b>Volume (%)</b><input name="volume_percent" type="number" min="0" max="100" value="{item.volume_percent if item.volume_percent is not None else ""}" placeholder="Invariato"></label>{advanced_fields(item)}<label class="field"><b>Messaggio predefinito</b><textarea name="default_message" maxlength="500">{_e(item.default_message or "")}</textarea></label><button>Salva routine</button></form></details><form method="post" action="/alexa-routines/routines/{item.id}/test"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}"><button>Prova</button></form><form method="post" action="/alexa-routines/routines/{item.id}/delete"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}"><button class="danger">Elimina</button></form></td>'
            "</tr>"
            for item in routines
        )
        alert_rows = "".join(
            "<tr>"
            f"<td>{_e(item.target_entity.voice_name or item.target_entity.friendly_name or item.target_entity.ha_entity_id)}</td>"
            f"<td>{_e(item.expected_state)}</td><td>{_e(item.status)}</td><td>{_e(item.message)}</td>"
            f'<td><form method="post" action="/alexa-routines/alerts/{item.id}/cancel"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}"><button class="danger"{" disabled" if item.status != "pending" else ""}>Annulla</button></form></td></tr>'
            for item in alerts
        )
        execution_rows = "".join(
            f"<tr><td>{item.created_at.astimezone(ROME).strftime('%d/%m %H:%M:%S')}</td>"
            f"<td>{_e(item.destination)}</td><td>{_e(item.message_preview)}</td>"
            f"<td>{str(item.volume_percent) + '%' if item.volume_percent is not None else '—'}</td>"
            f"<td>{_e(item.status)} ({item.succeeded}/{item.attempted})</td><td>{_e(item.detail or '—')}</td></tr>"
            for item in executions
        )
        notice = request.query_params.get("notice", "")
        notice_html = (
            f'<p class="{"ok" if notice == "sent" else "bad"}">{_e(request.query_params.get("message", ""))}</p>'
            if notice
            else ""
        )
        empty = (
            ""
            if speakers
            else '<p class="bad">Nessun Echo sincronizzato. Esponi le entità notify di Alexa Devices nelle opzioni del componente Ekonex Voice.</p>'
        )
        body = f"""
{notice_html}<form method="get"><label>Impianto <select name="installation" onchange="this.form.submit()">{installation_options}</select></label></form>
<div class="card"><h2>Invia annuncio di prova</h2>{empty}<form method="post" action="/alexa-routines/test">
<input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}">
<label class="field"><b>Destinatario</b><select name="destination">{destination_options}</select></label>
<label class="field"><b>Modalità</b><select name="mode"><option value="announce">Annuncio con suono</option><option value="speak">Parla senza suono</option></select></label>
<label class="field"><b>Volume (%) (facoltativo)</b><input name="volume_percent" type="number" min="0" max="100" step="1" placeholder="Lascia invariato"></label>
<label class="field"><b>Messaggio</b><textarea name="message" maxlength="500" required></textarea></label><button{" disabled" if not speakers else ""}>Invia prova</button></form></div>
<div class="card"><h2>Crea gruppo</h2><form method="post" action="/alexa-routines/groups">
<input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}">
<label class="field"><b>Nome gruppo</b><input name="name" maxlength="120" required></label>{speaker_options or '<p class="muted">Sincronizza prima gli Echo.</p>'}<button{" disabled" if not speakers else ""}>Crea gruppo</button></form></div>
<table><thead><tr><th>Gruppo</th><th>Echo inclusi</th><th>Azioni</th></tr></thead><tbody>
<tr><td><b>Ovunque</b><br><span class="muted">automatico</span></td><td>{len(speakers)} Echo sincronizzati</td><td>Non eliminabile</td></tr>
{group_rows or '<tr><td colspan="3">Nessun gruppo personalizzato</td></tr>'}</tbody></table>"""
        body += f"""<div class="card"><h2>Crea routine richiamabile da Home Assistant</h2>
<p class="muted">Il trigger Home Assistant richiama lo slug. Il messaggio del trigger sostituisce quello predefinito e può contenere valori di sensori già elaborati dai template di Home Assistant.</p>
<form method="post" action="/alexa-routines/routines"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}">
<label class="field"><b>Nome routine</b><input name="name" maxlength="120" required></label>
<label class="field"><b>Destinatario</b><select name="destination">{destination_options}</select></label>
<label class="field"><b>Modalità</b><select name="mode"><option value="announce">Annuncio con suono</option><option value="speak">Parla senza suono</option></select></label>
<label class="field"><b>Volume (%) (facoltativo)</b><input name="volume_percent" type="number" min="0" max="100" step="1" placeholder="Lascia invariato"></label>
{advanced_fields()}
<label class="field"><b>Messaggio predefinito (facoltativo)</b><textarea name="default_message" maxlength="500"></textarea></label>
<button{" disabled" if not speakers else ""}>Crea routine</button></form></div>
<table><thead><tr><th>Routine / slug</th><th>Destinatario</th><th>Modalità</th><th>Messaggio</th><th>Azioni</th></tr></thead><tbody>{routine_rows or '<tr><td colspan="5">Nessuna routine configurata</td></tr>'}</tbody></table>"""
        body += f"""<div class="card"><h2>Avvisi richiesti ad Alexa</h2><p class="muted">Avvisi automatici creati dicendo: avvisami quando...</p></div>
<table><thead><tr><th>Entità</th><th>Stato atteso</th><th>Stato avviso</th><th>Messaggio</th><th>Azioni</th></tr></thead><tbody>{alert_rows or '<tr><td colspan="5">Nessun avviso vocale</td></tr>'}</tbody></table>"""
        body += f"""<div class="card"><h2>Storico annunci</h2></div>
<table><thead><tr><th>Quando</th><th>Destinazione</th><th>Messaggio</th><th>Volume</th><th>Esito</th><th>Dettaglio</th></tr></thead><tbody>{execution_rows or '<tr><td colspan="6">Nessuna esecuzione registrata</td></tr>'}</tbody></table>"""
    response = HTMLResponse(_layout("Routine vocali", body, context, csrf, "alexa-routines"))
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        secure=get_settings().environment == "production",
        httponly=True,
        samesite="lax",
        path="/",
        max_age=1800,
    )
    return response


@router.post("/alerts/{alert_id}/cancel")
async def cancel_alert(
    alert_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _laboratory_only()
    _admin(context)
    values = await _multi_form(request)
    if not _valid_csrf(_one(values, "csrf_token"), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    installation = await _installation(session, context, UUID(_one(values, "installation_id")))
    alert = await session.scalar(
        select(AlexaVoiceAlert).where(
            AlexaVoiceAlert.id == alert_id,
            AlexaVoiceAlert.tenant_id == context.tenant_id,
            AlexaVoiceAlert.installation_id == installation.id,
        )
    )
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Avviso non trovato")
    if alert.status == "pending":
        alert.status = "cancelled"
        await session.commit()
    return _portal_redirect(installation.id)


@router.post("/groups")
async def create_group(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _laboratory_only()
    _admin(context)
    values = await _multi_form(request)
    if not _valid_csrf(_one(values, "csrf_token"), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    installation = await _installation(session, context, UUID(_one(values, "installation_id")))
    name = _one(values, "name")
    if not name or len(name) > 120:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nome gruppo non valido")
    requested_ids = {UUID(value) for value in values.get("member", [])}
    valid_speakers = {
        item.id: item
        for item in await _speakers(session, installation.id)
        if item.id in requested_ids
    }
    if not requested_ids or set(valid_speakers) != requested_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Selezione Echo non valida")
    group = AlexaSpeakerGroup(
        tenant_id=context.tenant_id,
        installation_id=installation.id,
        name=name,
        slug=category_slug(name),
    )
    group.members = [AlexaSpeakerGroupMember(entity_id=item.id) for item in valid_speakers.values()]
    session.add(group)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return _portal_redirect(
            installation.id,
            notice="error",
            message="Esiste già un gruppo con questo nome. Scegli un nome diverso.",
        )
    return _portal_redirect(installation.id)


@router.post("/groups/{group_id}/delete")
async def delete_group(
    group_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _laboratory_only()
    _admin(context)
    values = await _multi_form(request)
    if not _valid_csrf(_one(values, "csrf_token"), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    installation = await _installation(session, context, UUID(_one(values, "installation_id")))
    await session.execute(
        delete(AlexaVoiceRoutine).where(
            AlexaVoiceRoutine.tenant_id == context.tenant_id,
            AlexaVoiceRoutine.installation_id == installation.id,
            AlexaVoiceRoutine.destination_type == "group",
            AlexaVoiceRoutine.destination_id == group_id,
        )
    )
    await session.execute(
        delete(AlexaSpeakerGroup).where(
            AlexaSpeakerGroup.id == group_id,
            AlexaSpeakerGroup.tenant_id == context.tenant_id,
            AlexaSpeakerGroup.installation_id == installation.id,
        )
    )
    await session.commit()
    return RedirectResponse(
        f"/alexa-routines?{urlencode({'installation': str(installation.id)})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/groups/{group_id}")
async def update_group(
    group_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _laboratory_only()
    _admin(context)
    values = await _multi_form(request)
    if not _valid_csrf(_one(values, "csrf_token"), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    installation = await _installation(session, context, UUID(_one(values, "installation_id")))
    group = await session.scalar(
        select(AlexaSpeakerGroup)
        .options(selectinload(AlexaSpeakerGroup.members))
        .where(
            AlexaSpeakerGroup.id == group_id,
            AlexaSpeakerGroup.tenant_id == context.tenant_id,
            AlexaSpeakerGroup.installation_id == installation.id,
        )
    )
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gruppo non trovato")
    name = _one(values, "name")
    try:
        requested_ids = {UUID(value) for value in values.get("member", [])}
        valid = {
            item.id: item
            for item in await _speakers(session, installation.id)
            if item.id in requested_ids
        }
        if not name or len(name) > 120 or not requested_ids or set(valid) != requested_ids:
            raise ValueError
        group.name = name
        group.slug = category_slug(name)
        group.members = _updated_group_members(group, valid)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return _portal_redirect(
            installation.id,
            notice="error",
            message="Esiste già un gruppo con questo nome. Scegli un nome diverso.",
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Gruppo non valido") from error
    return _portal_redirect(installation.id)


async def _destination_speakers(
    session: AsyncSession,
    tenant_id: UUID,
    installation: Installation,
    destination: str,
) -> list[Entity]:
    available = await _speakers(session, installation.id)
    if destination == "all":
        return available
    if destination == "last":
        speaker_by_device = {
            item.device_id: item for item in available if item.device_id is not None
        }
        if not speaker_by_device:
            return []
        last_voice_event = await session.scalar(
            select(Entity)
            .where(
                Entity.installation_id == installation.id,
                Entity.ha_domain == "event",
                Entity.device_id.in_(speaker_by_device),
                Entity.last_changed_at.is_not(None),
                Entity.deleted_at.is_(None),
            )
            .order_by(Entity.last_changed_at.desc())
            .limit(1)
        )
        if last_voice_event is None or last_voice_event.device_id is None:
            return []
        speaker = speaker_by_device.get(last_voice_event.device_id)
        return [speaker] if speaker is not None else []
    if destination.startswith("entity:"):
        requested = UUID(destination.removeprefix("entity:"))
        return [item for item in available if item.id == requested]
    if destination.startswith("group:"):
        group_id = UUID(destination.removeprefix("group:"))
        group = await session.scalar(
            select(AlexaSpeakerGroup)
            .options(selectinload(AlexaSpeakerGroup.members))
            .where(
                AlexaSpeakerGroup.id == group_id,
                AlexaSpeakerGroup.tenant_id == tenant_id,
                AlexaSpeakerGroup.installation_id == installation.id,
                AlexaSpeakerGroup.enabled.is_(True),
            )
        )
        member_ids = {item.entity_id for item in group.members} if group else set()
        return [item for item in available if item.id in member_ids]
    return []


async def _mode_entity(
    session: AsyncSession, installation_id: UUID, canonical: Entity, mode: str
) -> Entity | None:
    if mode == "announce":
        return canonical
    if canonical.device_id:
        result: Entity | None = await session.scalar(
            select(Entity).where(
                Entity.installation_id == installation_id,
                Entity.device_id == canonical.device_id,
                Entity.ha_domain == "notify",
                or_(*(Entity.ha_entity_id.endswith(suffix) for suffix in SPEAK_SUFFIXES)),
                Entity.deleted_at.is_(None),
            )
        )
        return result
    stem = canonical.ha_entity_id
    for suffix in ANNOUNCE_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem.removesuffix(suffix)
            break
    result = await session.scalar(
        select(Entity).where(
            Entity.installation_id == installation_id,
            or_(*(Entity.ha_entity_id == stem + suffix for suffix in SPEAK_SUFFIXES)),
            Entity.deleted_at.is_(None),
        )
    )
    return result


async def _volume_entity(
    session: AsyncSession, installation_id: UUID, canonical: Entity
) -> Entity | None:
    if not canonical.device_id:
        return None
    result: Entity | None = await session.scalar(
        select(Entity).where(
            Entity.installation_id == installation_id,
            Entity.device_id == canonical.device_id,
            Entity.ha_domain == "media_player",
            Entity.deleted_at.is_(None),
        )
    )
    return result


async def _all_have_volume_entity(
    session: AsyncSession, installation_id: UUID, speakers: list[Entity]
) -> bool:
    for speaker in speakers:
        if await _volume_entity(session, installation_id, speaker) is None:
            return False
    return True


def _split_destination(destination: str) -> tuple[str, UUID | None]:
    if destination == "all":
        return "all", None
    if destination == "last":
        return "last", None
    kind, separator, raw_id = destination.partition(":")
    if separator and kind in {"entity", "group"}:
        return kind, UUID(raw_id)
    raise ValueError("invalid destination")


def _routine_destination(routine: AlexaVoiceRoutine) -> str:
    return (
        routine.destination_type
        if routine.destination_type in {"all", "last"}
        else f"{routine.destination_type}:{routine.destination_id}"
    )


def _test_outcome_summary(outcomes: list[DispatchOutcome]) -> tuple[str, str]:
    attempted = len(outcomes)
    succeeded = sum(item.status == "success" for item in outcomes)
    if attempted and succeeded == attempted:
        return "sent", f"Annuncio inviato a {succeeded} Echo."
    if succeeded:
        return "partial", f"Annuncio inviato a {succeeded} di {attempted} Echo."
    return "failed", "Annuncio non eseguito: verifica collegamento e componente beta."


async def _dispatch_announcement_now(
    session: AsyncSession,
    tenant_id: UUID,
    installation: Installation,
    destination: str,
    mode: str,
    message: str,
    volume_percent: int | None = None,
    repeat_count: int = 1,
    repeat_interval_seconds: int = 1,
    restore_volume: bool = False,
) -> list[DispatchOutcome]:
    command = command_adapter.validate_python({"operation": mode, "message": message})
    canonical = await _destination_speakers(session, tenant_id, installation, destination)
    targets: list[tuple[Entity, Entity]] = []
    for item in canonical:
        speech = await _mode_entity(session, installation.id, item, mode)
        if speech is not None and speech.ha_registry_id is not None:
            targets.append((item, speech))
    if not targets:
        raise ValueError("no compatible speakers")
    outcomes: list[DispatchOutcome] = []
    dispatcher = CommandDispatchService(session, sessions)
    for canonical_target, speech_target in targets:
        previous_volume: int | None = None
        if volume_percent is not None:
            volume_target = await _volume_entity(session, installation.id, canonical_target)
            if volume_target is None or volume_target.ha_registry_id is None:
                raise ValueError("volume entity unavailable")
            volume_command = command_adapter.validate_python(
                {"operation": "set_volume", "volume_percent": volume_percent}
            )
            volume_outcome = await dispatcher.dispatch(
                installation.id, volume_target.ha_registry_id, volume_command
            )
            if volume_outcome.status != "success":
                outcomes.append(volume_outcome)
                continue
            raw_previous = volume_target.attributes_json.get("volume_level")
            if isinstance(raw_previous, (int, float)) and not isinstance(raw_previous, bool):
                previous_volume = round(float(raw_previous) * 100)
            # Home Assistant confirms the service call before some Echo devices have
            # applied the new level. Avoid announcing with the previous volume.
            await asyncio.sleep(0.75)
        if speech_target.ha_registry_id is not None:
            for repetition in range(repeat_count):
                outcomes.append(
                    await dispatcher.dispatch(
                        installation.id, speech_target.ha_registry_id, command
                    )
                )
                if repetition + 1 < repeat_count:
                    await asyncio.sleep(repeat_interval_seconds)
        if restore_volume and previous_volume is not None:
            assert volume_target is not None and volume_target.ha_registry_id is not None
            await asyncio.sleep(1)
            restore_command = command_adapter.validate_python(
                {"operation": "set_volume", "volume_percent": previous_volume}
            )
            await dispatcher.dispatch(
                installation.id, volume_target.ha_registry_id, restore_command
            )
    return outcomes


async def _announcement_worker(installation_id: UUID) -> None:
    queue = _announcement_queues[installation_id]
    while True:
        _, _, future, operation = await queue.get()
        try:
            result = await operation()
            if not future.cancelled():
                future.set_result(result)
        except Exception as error:
            if not future.cancelled():
                future.set_exception(error)
        finally:
            queue.task_done()


async def _dispatch_announcement(
    session: AsyncSession,
    tenant_id: UUID,
    installation: Installation,
    destination: str,
    mode: str,
    message: str,
    volume_percent: int | None = None,
    repeat_count: int = 1,
    repeat_interval_seconds: int = 1,
    restore_volume: bool = False,
    priority: int = 5,
) -> list[DispatchOutcome]:
    queue = _announcement_queues.setdefault(installation.id, asyncio.PriorityQueue())
    worker = _announcement_workers.get(installation.id)
    if worker is None or worker.done():
        _announcement_workers[installation.id] = asyncio.create_task(
            _announcement_worker(installation.id)
        )
    future: asyncio.Future[list[DispatchOutcome]] = asyncio.get_running_loop().create_future()

    async def operation() -> list[DispatchOutcome]:
        return await _dispatch_announcement_now(
            session,
            tenant_id,
            installation,
            destination,
            mode,
            message,
            volume_percent,
            repeat_count,
            repeat_interval_seconds,
            restore_volume,
        )

    await queue.put((priority, next(_queue_sequence), future, operation))
    return await future


@router.post("/routines")
async def create_routine(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _laboratory_only()
    _admin(context)
    values = await _multi_form(request)
    if not _valid_csrf(_one(values, "csrf_token"), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    installation = await _installation(session, context, UUID(_one(values, "installation_id")))
    name = _one(values, "name")
    message = _one(values, "default_message") or None
    mode = _one(values, "mode")
    raw_volume = _one(values, "volume_percent")
    try:
        advanced = _advanced_options(values)
        slug = category_slug(name)
        destination_type, destination_id = _split_destination(_one(values, "destination"))
        if mode not in {"announce", "speak"} or (message and len(message) > 500):
            raise ValueError
        volume_percent = int(raw_volume) if raw_volume else None
        if volume_percent is not None and not 0 <= volume_percent <= 100:
            raise ValueError
        if message:
            command_adapter.validate_python({"operation": mode, "message": message})
        # Resolve now so stale/cross-tenant target identifiers cannot be persisted.
        selected_speakers = await _destination_speakers(
            session, context.tenant_id, installation, _one(values, "destination")
        )
        if not selected_speakers:
            raise ValueError
        if not await _condition_belongs_to_tenant(
            session, context.tenant_id, advanced["condition_entity_id"]
        ):
            raise ValueError
        if volume_percent is not None and not await _all_have_volume_entity(
            session, installation.id, selected_speakers
        ):
            raise ValueError
    except (ValueError, ValidationError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Routine non valida") from error
    session.add(
        AlexaVoiceRoutine(
            tenant_id=context.tenant_id,
            installation_id=installation.id,
            name=name,
            slug=slug,
            mode=mode,
            destination_type=destination_type,
            destination_id=destination_id,
            default_message=message,
            volume_percent=volume_percent,
            **advanced,
        )
    )
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Esiste già una routine con questo nome"
        ) from error
    return RedirectResponse(
        f"/alexa-routines?{urlencode({'installation': str(installation.id)})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/routines/{routine_id}/delete")
async def delete_routine(
    routine_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _laboratory_only()
    _admin(context)
    values = await _multi_form(request)
    if not _valid_csrf(_one(values, "csrf_token"), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    installation = await _installation(session, context, UUID(_one(values, "installation_id")))
    await session.execute(
        delete(AlexaVoiceRoutine).where(
            AlexaVoiceRoutine.id == routine_id,
            AlexaVoiceRoutine.tenant_id == context.tenant_id,
            AlexaVoiceRoutine.installation_id == installation.id,
        )
    )
    await session.commit()
    return RedirectResponse(
        f"/alexa-routines?{urlencode({'installation': str(installation.id)})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/routines/{routine_id}")
async def update_routine(
    routine_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _laboratory_only()
    _admin(context)
    values = await _multi_form(request)
    if not _valid_csrf(_one(values, "csrf_token"), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    installation = await _installation(session, context, UUID(_one(values, "installation_id")))
    routine = await session.scalar(
        select(AlexaVoiceRoutine).where(
            AlexaVoiceRoutine.id == routine_id,
            AlexaVoiceRoutine.tenant_id == context.tenant_id,
            AlexaVoiceRoutine.installation_id == installation.id,
        )
    )
    if routine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routine non trovata")
    name = _one(values, "name")
    message = _one(values, "default_message") or None
    mode = _one(values, "mode")
    raw_volume = _one(values, "volume_percent")
    destination = _one(values, "destination")
    try:
        advanced = _advanced_options(values)
        destination_type, destination_id = _split_destination(destination)
        volume_percent = int(raw_volume) if raw_volume else None
        selected_speakers = await _destination_speakers(
            session, context.tenant_id, installation, destination
        )
        if (
            mode not in {"announce", "speak"}
            or (message and len(message) > 500)
            or (volume_percent is not None and not 0 <= volume_percent <= 100)
            or not selected_speakers
        ):
            raise ValueError
        if volume_percent is not None and not await _all_have_volume_entity(
            session, installation.id, selected_speakers
        ):
            raise ValueError
        if not await _condition_belongs_to_tenant(
            session, context.tenant_id, advanced["condition_entity_id"]
        ):
            raise ValueError
        if message:
            command_adapter.validate_python({"operation": mode, "message": message})
        routine.name = name
        routine.slug = category_slug(name)
        routine.mode = mode
        routine.destination_type = destination_type
        routine.destination_id = destination_id
        routine.default_message = message
        routine.volume_percent = volume_percent
        for field, value in advanced.items():
            setattr(routine, field, value)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Esiste già una routine con questo nome"
        ) from error
    except (ValueError, ValidationError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Routine non valida") from error
    return RedirectResponse(
        f"/alexa-routines?{urlencode({'installation': str(installation.id)})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/routines/{routine_id}/test")
async def test_routine(
    routine_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _laboratory_only()
    _admin(context)
    values = await _multi_form(request)
    if not _valid_csrf(_one(values, "csrf_token"), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    installation = await _installation(session, context, UUID(_one(values, "installation_id")))
    routine = await session.scalar(
        select(AlexaVoiceRoutine).where(
            AlexaVoiceRoutine.id == routine_id,
            AlexaVoiceRoutine.tenant_id == context.tenant_id,
            AlexaVoiceRoutine.installation_id == installation.id,
        )
    )
    if routine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routine non trovata")
    if not routine.default_message:
        return _portal_redirect(
            installation.id, notice="error", message="Inserisci prima un messaggio predefinito."
        )
    if not await _routine_allowed(session, routine):
        return _portal_redirect(
            installation.id, notice="error", message="Condizioni della routine non soddisfatte."
        )
    message = await _render_variables(session, context.tenant_id, routine.default_message)
    message = {"bell": "Din don. ", "warning": "Attenzione. ", "emergency": "Avviso urgente. "}.get(
        routine.sound, ""
    ) + message
    mode = "speak" if routine.sound == "none" else routine.mode
    volume = _routine_volume(routine)
    outcomes = await _dispatch_announcement(
        session,
        context.tenant_id,
        installation,
        _routine_destination(routine),
        mode,
        message,
        volume,
        routine.repeat_count,
        routine.repeat_interval_seconds,
        routine.restore_volume,
        routine.priority,
    )
    succeeded = sum(item.status == "success" for item in outcomes)
    result = "success" if succeeded == len(outcomes) else "partial" if succeeded else "failed"
    session.add(
        AlexaRoutineExecution(
            tenant_id=context.tenant_id,
            installation_id=installation.id,
            routine_id=routine.id,
            destination=_routine_destination(routine),
            mode=mode,
            volume_percent=volume,
            message_preview=message[:120],
            attempted=len(outcomes),
            succeeded=succeeded,
            status=result,
            detail=None if result == "success" else "Uno o più Echo non hanno risposto",
        )
    )
    await session.commit()
    notice, detail = _test_outcome_summary(outcomes)
    return _portal_redirect(installation.id, notice=notice, message=detail)


@connector_router.post("/{routine_slug}/trigger", response_model=RoutineTriggerResponse)
async def trigger_routine(
    routine_slug: str,
    payload: RoutineTriggerRequest,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = session_dependency,
) -> RoutineTriggerResponse:
    """Run one lab routine using the installation-bound Connector credential."""
    _laboratory_only()
    credential: ConnectorCredential = await authenticate_connector_secret(authorization, session)
    installation = credential.installation
    routine = await session.scalar(
        select(AlexaVoiceRoutine).where(
            AlexaVoiceRoutine.installation_id == installation.id,
            AlexaVoiceRoutine.tenant_id == installation.tenant_id,
            AlexaVoiceRoutine.slug == routine_slug,
            AlexaVoiceRoutine.enabled.is_(True),
        )
    )
    if routine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routine non trovata")
    if not await _routine_allowed(session, routine):
        session.add(
            AlexaRoutineExecution(
                tenant_id=routine.tenant_id,
                installation_id=routine.installation_id,
                routine_id=routine.id,
                destination=_routine_destination(routine),
                mode=routine.mode,
                volume_percent=_routine_volume(routine),
                message_preview="Condizioni non soddisfatte",
                status="skipped",
                detail="Condizioni della routine non soddisfatte",
            )
        )
        await session.commit()
        return RoutineTriggerResponse(
            routine=routine.slug, attempted=0, succeeded=0, status="success"
        )
    message = payload.message or routine.default_message
    if not message:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Messaggio richiesto")
    message = await _render_variables(session, installation.tenant_id, message)
    sound_prefix = {
        "bell": "Din don. ",
        "warning": "Attenzione. ",
        "emergency": "Avviso urgente. ",
    }.get(routine.sound, "")
    message = sound_prefix + message
    effective_mode = "speak" if routine.sound == "none" else routine.mode
    volume = _routine_volume(routine)
    try:
        outcomes = await _dispatch_announcement(
            session,
            installation.tenant_id,
            installation,
            _routine_destination(routine),
            effective_mode,
            message,
            volume,
            routine.repeat_count,
            routine.repeat_interval_seconds,
            routine.restore_volume,
            routine.priority,
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Routine non eseguibile"
        ) from error
    succeeded = sum(getattr(item, "status", None) == "success" for item in outcomes)
    result = "success" if succeeded == len(outcomes) else "partial" if succeeded else "failed"
    session.add(
        AlexaRoutineExecution(
            tenant_id=routine.tenant_id,
            installation_id=routine.installation_id,
            routine_id=routine.id,
            destination=_routine_destination(routine),
            mode=effective_mode,
            volume_percent=volume,
            message_preview=message[:120],
            attempted=len(outcomes),
            succeeded=succeeded,
            status=result,
            detail=None if result == "success" else "Uno o più Echo non hanno risposto",
        )
    )
    await session.commit()
    return RoutineTriggerResponse(
        routine=routine.slug, attempted=len(outcomes), succeeded=succeeded, status=result
    )


@router.post("/test")
async def send_test(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _laboratory_only()
    _admin(context)
    values = await _multi_form(request)
    if not _valid_csrf(_one(values, "csrf_token"), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    installation = await _installation(session, context, UUID(_one(values, "installation_id")))
    mode, message = _one(values, "mode"), _one(values, "message")
    raw_volume = _one(values, "volume_percent")
    try:
        volume_percent = int(raw_volume) if raw_volume else None
        if volume_percent is not None and not 0 <= volume_percent <= 100:
            raise ValueError
        outcomes = await _dispatch_announcement(
            session,
            context.tenant_id,
            installation,
            _one(values, "destination"),
            mode,
            message,
            volume_percent,
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Annuncio non valido") from error
    notice, detail = _test_outcome_summary(outcomes)
    succeeded = sum(item.status == "success" for item in outcomes)
    session.add(
        AlexaRoutineExecution(
            tenant_id=context.tenant_id,
            installation_id=installation.id,
            routine_id=None,
            destination=_one(values, "destination"),
            mode=mode,
            volume_percent=volume_percent,
            message_preview=message[:120],
            attempted=len(outcomes),
            succeeded=succeeded,
            status=notice,
            detail=detail,
        )
    )
    await session.commit()
    query = urlencode(
        {
            "installation": str(installation.id),
            "notice": notice,
            "message": detail,
        }
    )
    return RedirectResponse(f"/alexa-routines?{query}", status_code=status.HTTP_303_SEE_OTHER)
