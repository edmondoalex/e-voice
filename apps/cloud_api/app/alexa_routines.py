"""Laboratory portal for Alexa Devices speakers, groups and manual announcements."""

# HTML is intentionally inline for this isolated server-rendered laboratory page.
# ruff: noqa: E501

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import parse_qs, urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select
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
    AlexaSpeakerGroup,
    AlexaSpeakerGroupMember,
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


class RoutineTriggerRequest(BaseModel):
    """Bounded message rendered by Home Assistant before transport."""

    model_config = ConfigDict(extra="forbid", strict=True)
    message: str | None = Field(default=None, max_length=500)


class RoutineTriggerResponse(BaseModel):
    routine: str
    attempted: int
    succeeded: int
    status: Literal["success", "partial", "failed"]


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
                    Entity.ha_entity_id.endswith("_announce"),
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


def _speaker_name(entity: Entity) -> str:
    return entity.display_name or entity.friendly_name or entity.ha_entity_id


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
            choices = [("all", "Ovunque (automatico)")]
            choices.extend((f"entity:{item.id}", _speaker_name(item)) for item in speakers)
            choices.extend((f"group:{group.id}", f"Gruppo: {group.name}") for group in groups)
            return "".join(
                f'<option value="{value}"{" selected" if value == selected else ""}>{_e(label)}</option>'
                for value, label in choices
            )

        destination_options = destination_options_for()
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
            **{f"entity:{item.id}": _speaker_name(item) for item in speakers},
            **{f"group:{item.id}": f"Gruppo: {item.name}" for item in groups},
        }
        routine_rows = "".join(
            "<tr>"
            f"<td><b>{_e(item.name)}</b><br><span class=muted>{_e(item.slug)}</span></td>"
            f"<td>{_e(destination_names.get(f'{item.destination_type}:{item.destination_id or ""}', 'Destinazione non disponibile'))}</td>"
            f"<td>{_e(item.mode)}<br><span class=muted>Volume: {_e(str(item.volume_percent) + '%' if item.volume_percent is not None else 'invariato')}</span></td><td>{_e(item.default_message or 'Messaggio fornito dal trigger Home Assistant')}</td>"
            f'<td><details><summary>Modifica</summary><form method="post" action="/alexa-routines/routines/{item.id}"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}"><label class="field"><b>Nome</b><input name="name" maxlength="120" value="{_e(item.name)}" required></label><label class="field"><b>Destinatario</b><select name="destination">{destination_options_for(_routine_destination(item))}</select></label><label class="field"><b>Modalità</b><select name="mode"><option value="announce"{" selected" if item.mode == "announce" else ""}>Annuncio</option><option value="speak"{" selected" if item.mode == "speak" else ""}>Parla</option></select></label><label class="field"><b>Volume</b><input name="volume_percent" type="number" min="0" max="100" value="{item.volume_percent if item.volume_percent is not None else ""}" placeholder="Invariato"></label><label class="field"><b>Messaggio predefinito</b><textarea name="default_message" maxlength="500">{_e(item.default_message or "")}</textarea></label><button>Salva routine</button></form></details><form method="post" action="/alexa-routines/routines/{item.id}/delete"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{installation.id}"><button class="danger">Elimina</button></form></td>'
            "</tr>"
            for item in routines
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
<label class="field"><b>Volume (facoltativo)</b><input name="volume_percent" type="number" min="0" max="100" step="1" placeholder="Lascia invariato"></label>
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
<label class="field"><b>Messaggio predefinito (facoltativo)</b><textarea name="default_message" maxlength="500"></textarea></label>
<button{" disabled" if not speakers else ""}>Crea routine</button></form></div>
<table><thead><tr><th>Routine / slug</th><th>Destinatario</th><th>Modalità</th><th>Messaggio</th><th>Azioni</th></tr></thead><tbody>{routine_rows or '<tr><td colspan="5">Nessuna routine configurata</td></tr>'}</tbody></table>"""
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
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Esiste già un gruppo con questo nome"
        ) from error
    return RedirectResponse(
        f"/alexa-routines?{urlencode({'installation': str(installation.id)})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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
        group.members = [AlexaSpeakerGroupMember(entity_id=item.id) for item in valid.values()]
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Esiste già un gruppo con questo nome"
        ) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Gruppo non valido") from error
    return RedirectResponse(
        f"/alexa-routines?{urlencode({'installation': str(installation.id)})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _destination_speakers(
    session: AsyncSession,
    tenant_id: UUID,
    installation: Installation,
    destination: str,
) -> list[Entity]:
    available = await _speakers(session, installation.id)
    if destination == "all":
        return available
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
                Entity.ha_entity_id.endswith("_speak"),
                Entity.deleted_at.is_(None),
            )
        )
        return result
    expected = canonical.ha_entity_id.removesuffix("_announce") + "_speak"
    result = await session.scalar(
        select(Entity).where(
            Entity.installation_id == installation_id,
            Entity.ha_entity_id == expected,
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
    kind, separator, raw_id = destination.partition(":")
    if separator and kind in {"entity", "group"}:
        return kind, UUID(raw_id)
    raise ValueError("invalid destination")


def _routine_destination(routine: AlexaVoiceRoutine) -> str:
    return (
        "all"
        if routine.destination_type == "all"
        else f"{routine.destination_type}:{routine.destination_id}"
    )


async def _dispatch_announcement(
    session: AsyncSession,
    tenant_id: UUID,
    installation: Installation,
    destination: str,
    mode: str,
    message: str,
    volume_percent: int | None = None,
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
        if speech_target.ha_registry_id is not None:
            outcomes.append(
                await dispatcher.dispatch(installation.id, speech_target.ha_registry_id, command)
            )
    return outcomes


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
        if message:
            command_adapter.validate_python({"operation": mode, "message": message})
        routine.name = name
        routine.slug = category_slug(name)
        routine.mode = mode
        routine.destination_type = destination_type
        routine.destination_id = destination_id
        routine.default_message = message
        routine.volume_percent = volume_percent
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
    message = payload.message or routine.default_message
    if not message:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Messaggio richiesto")
    try:
        outcomes = await _dispatch_announcement(
            session,
            installation.tenant_id,
            installation,
            _routine_destination(routine),
            routine.mode,
            message,
            routine.volume_percent,
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Routine non eseguibile"
        ) from error
    succeeded = sum(getattr(item, "status", None) == "success" for item in outcomes)
    result = "success" if succeeded == len(outcomes) else "partial" if succeeded else "failed"
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
    try:
        outcomes = await _dispatch_announcement(
            session,
            context.tenant_id,
            installation,
            _one(values, "destination"),
            mode,
            message,
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Annuncio non valido") from error
    succeeded = bool(outcomes) and all(item.status == "success" for item in outcomes)
    detail = (
        f"Annuncio inviato a {len(outcomes)} Echo."
        if succeeded
        else "Annuncio non eseguito: verifica collegamento e componente beta."
    )
    query = urlencode(
        {
            "installation": str(installation.id),
            "notice": "sent" if succeeded else "failed",
            "message": detail,
        }
    )
    return RedirectResponse(f"/alexa-routines?{query}", status_code=status.HTTP_303_SEE_OTHER)
