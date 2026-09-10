"""Laboratory DoorBird inputs, with up to five buttons per installation."""

# ruff: noqa: E501
from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .admin_console import _admin, _e, _installation, _layout, console_context_dependency
from .alexa_routines import (
    _dispatch_announcement,
    _multi_form,
    _one,
    _render_variables,
    _routine_allowed,
    _routine_destination,
    _routine_volume,
)
from .auth import TenantContext
from .config import get_settings
from .database import get_database_session
from .domain.models import AlexaRoutineExecution, AlexaVoiceRoutine, DoorbellButton, Installation
from .pairing_api import CSRF_COOKIE, _csrf, _valid_csrf

router = APIRouter(prefix="/doorbells", tags=["doorbells"])
public_router = APIRouter(prefix="/integrations/doorbell", tags=["doorbell-ingress"])
session_dependency = Depends(get_database_session)
MAX_BUTTONS = 5


def _laboratory_only() -> None:
    if get_settings().environment != "laboratory":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Non trovato")


def _cipher() -> Fernet:
    return Fernet(get_settings().pairing_delivery_key.encode())


def _token(button: DoorbellButton) -> str:
    try:
        return _cipher().decrypt(button.token_encrypted).decode()
    except InvalidToken as error:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Token campanello non leggibile"
        ) from error


def _back(installation_id: UUID, kind: str, message: str) -> RedirectResponse:
    query = urlencode({"installation": str(installation_id), "notice": kind, "message": message})
    return RedirectResponse(f"/doorbells?{query}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("", response_class=HTMLResponse)
async def page(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    installation: UUID | None = None,
    notice: str | None = None,
    message: str | None = None,
) -> HTMLResponse:
    _laboratory_only()
    _admin(context)
    installations = list(
        (
            await session.scalars(
                select(Installation)
                .where(Installation.tenant_id == context.tenant_id)
                .order_by(Installation.name)
            )
        ).all()
    )
    selected = (
        await _installation(session, context, installation)
        if installation
        else (installations[0] if installations else None)
    )
    csrf = _csrf(context)
    banner = (
        f'<div class="card {"ok" if notice == "success" else "bad"}">{_e(message)}</div>'
        if message
        else ""
    )
    choices = "".join(
        f'<option value="{item.id}"{" selected" if selected and item.id == selected.id else ""}>{_e(item.name)}</option>'
        for item in installations
    )
    body = f'<h1>Campanelli DoorBird</h1>{banner}<div class="card"><form method="get"><label><b>Impianto</b> <select name="installation">{choices}</select></label> <button>Apri</button></form></div>'
    if selected:
        routines = list(
            (
                await session.scalars(
                    select(AlexaVoiceRoutine)
                    .where(
                        AlexaVoiceRoutine.installation_id == selected.id,
                        AlexaVoiceRoutine.enabled.is_(True),
                    )
                    .order_by(AlexaVoiceRoutine.name)
                )
            ).all()
        )
        buttons = list(
            (
                await session.scalars(
                    select(DoorbellButton)
                    .options(selectinload(DoorbellButton.routine))
                    .where(DoorbellButton.installation_id == selected.id)
                    .order_by(DoorbellButton.created_at)
                )
            ).all()
        )
        options = "".join(
            f'<option value="{item.id}">{_e(item.name)} — {_e(_routine_destination(item))}</option>'
            for item in routines
        )
        rows = "".join(
            f'<tr><td><b>{_e(item.name)}</b></td><td>{_e(item.routine.name)}</td><td><code style="overflow-wrap:anywhere">{_e(str(request.base_url).rstrip("/") + "/integrations/doorbell/" + str(item.id) + "?token=" + _token(item))}</code></td><td>{item.cooldown_seconds} s</td><td><form method="post" action="/doorbells/{item.id}/delete"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{selected.id}"><button class="danger">Elimina</button></form></td></tr>'
            for item in buttons
        )
        disabled = " disabled" if len(buttons) >= MAX_BUTTONS or not routines else ""
        body += f'''<div class="card"><h2>Nuovo pulsante ({len(buttons)}/{MAX_BUTTONS})</h2><p>Il messaggio e gli Echo destinatari sono quelli della routine scelta. L'indirizzo GET è pronto da copiare in DoorBird.</p><form method="post" action="/doorbells"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{selected.id}"><label class="field"><b>Nome campanello</b><input name="name" maxlength="80" placeholder="Famiglia Rossi" required></label><label class="field"><b>Routine e destinazione</b><select name="routine_id" required>{options}</select></label><label class="field"><b>Blocco doppi squilli (secondi)</b><input name="cooldown_seconds" type="number" min="1" max="60" value="5" required></label><button{disabled}>Crea campanello</button></form></div><table><thead><tr><th>Campanello</th><th>Routine</th><th>Indirizzo GET DoorBird</th><th>Anti-doppio</th><th>Azioni</th></tr></thead><tbody>{rows or '<tr><td colspan="5">Nessun campanello configurato.</td></tr>'}</tbody></table>'''
    response = HTMLResponse(_layout("Campanelli", body, context, csrf, "doorbells"))
    response.set_cookie(CSRF_COOKIE, csrf, httponly=True, secure=True, samesite="lax")
    return response


@router.post("")
async def create(
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
    count = await session.scalar(
        select(func.count())
        .select_from(DoorbellButton)
        .where(DoorbellButton.installation_id == installation.id)
    )
    if int(count or 0) >= MAX_BUTTONS:
        return _back(installation.id, "error", "Sono ammessi al massimo 5 campanelli per impianto.")
    routine = await session.scalar(
        select(AlexaVoiceRoutine).where(
            AlexaVoiceRoutine.id == UUID(_one(values, "routine_id")),
            AlexaVoiceRoutine.installation_id == installation.id,
            AlexaVoiceRoutine.tenant_id == context.tenant_id,
        )
    )
    name = _one(values, "name").strip()
    try:
        cooldown = int(_one(values, "cooldown_seconds"))
    except ValueError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Intervallo non valido"
        ) from error
    if routine is None or not name or not 1 <= cooldown <= 60:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Configurazione non valida")
    secret = secrets.token_urlsafe(32)
    session.add(
        DoorbellButton(
            tenant_id=context.tenant_id,
            installation_id=installation.id,
            routine_id=routine.id,
            name=name,
            token_encrypted=_cipher().encrypt(secret.encode()),
            cooldown_seconds=cooldown,
        )
    )
    await session.commit()
    return _back(
        installation.id, "success", "Campanello creato. Copia il suo indirizzo GET in DoorBird."
    )


@router.post("/{button_id}/delete")
async def remove(
    button_id: UUID,
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
        delete(DoorbellButton).where(
            DoorbellButton.id == button_id,
            DoorbellButton.installation_id == installation.id,
            DoorbellButton.tenant_id == context.tenant_id,
        )
    )
    await session.commit()
    return _back(installation.id, "success", "Campanello eliminato.")


@public_router.get("/{button_id}")
async def ring(
    button_id: UUID, token: str, session: Annotated[AsyncSession, session_dependency]
) -> JSONResponse:
    _laboratory_only()
    button = await session.scalar(
        select(DoorbellButton)
        .options(selectinload(DoorbellButton.routine))
        .where(DoorbellButton.id == button_id, DoorbellButton.enabled.is_(True))
    )
    if button is None or not secrets.compare_digest(token, _token(button)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campanello non trovato")
    now = datetime.now(UTC)
    last = button.last_triggered_at
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if (now - last).total_seconds() < button.cooldown_seconds:
            return JSONResponse({"status": "duplicate_ignored"})
    routine = button.routine
    installation = await session.get(Installation, button.installation_id)
    if installation is None or not routine.enabled or not routine.default_message:
        raise HTTPException(status.HTTP_409_CONFLICT, "Campanello non configurato")
    button.last_triggered_at = now
    await session.commit()
    if not await _routine_allowed(session, routine):
        return JSONResponse({"status": "condition_not_met"})
    message = await _render_variables(session, button.tenant_id, routine.default_message)
    mode = "speak" if routine.sound == "none" else routine.mode
    outcomes = await _dispatch_announcement(
        session,
        button.tenant_id,
        installation,
        _routine_destination(routine),
        mode,
        message,
        _routine_volume(routine),
        routine.repeat_count,
        routine.repeat_interval_seconds,
        routine.restore_volume,
        routine.priority,
    )
    succeeded = sum(item.status == "success" for item in outcomes)
    result = (
        "success"
        if outcomes and succeeded == len(outcomes)
        else "partial"
        if succeeded
        else "failed"
    )
    session.add(
        AlexaRoutineExecution(
            tenant_id=button.tenant_id,
            installation_id=button.installation_id,
            routine_id=routine.id,
            destination=_routine_destination(routine),
            mode=mode,
            volume_percent=_routine_volume(routine),
            message_preview=f"Campanello: {button.name}"[:120],
            attempted=len(outcomes),
            succeeded=succeeded,
            status=result,
            detail=None if result == "success" else "Uno o più Echo non hanno risposto",
        )
    )
    await session.commit()
    return JSONResponse({"status": result, "attempted": len(outcomes), "succeeded": succeeded})
