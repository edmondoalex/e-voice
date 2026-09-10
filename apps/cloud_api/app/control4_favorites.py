"""Configure and test local Control4 music favorite recalls."""

# ruff: noqa: E501, E701, E702
from __future__ import annotations

import ipaddress
import re
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .admin_console import _admin, _e, _installation, _layout, console_context_dependency
from .alexa_routines import _multi_form, _one
from .auth import TenantContext
from .config import get_settings
from .database import get_database_session
from .domain.models import Installation
from .evcp import sessions
from .pairing_api import CSRF_COOKIE, _csrf, _valid_csrf

router = APIRouter(prefix="/control4-favorites", tags=["control4-favorites"])
session_dependency = Depends(get_database_session)
COMMAND = re.compile(r"^[a-z0-9](?:[a-z0-9_/-]{0,126}[a-z0-9])?$")
PART = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_FAVORITES = 100


def _lab() -> None:
    if get_settings().environment != "laboratory":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Non trovato")


def _settings(installation: Installation) -> dict[str, object]:
    value = dict(installation.control4_favorites_json or {})
    value.setdefault("host", "")
    value.setdefault("port", 8081)
    value.setdefault("favorites", [])
    return value


def _valid_host(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_private and not address.is_loopback and not address.is_multicast


def _back(installation_id: UUID, kind: str, message: str) -> RedirectResponse:
    query = urlencode({"installation": str(installation_id), "notice": kind, "message": message})
    return RedirectResponse(f"/control4-favorites?{query}", status_code=303)


@router.get("", response_class=HTMLResponse)
async def page(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    installation: UUID | None = None,
    notice: str | None = None,
    message: str | None = None,
) -> HTMLResponse:
    _lab()
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
    body = f'<h1>Preferiti musicali Control4</h1>{banner}<div class="card"><form method="get"><b>Impianto</b> <select name="installation">{choices}</select> <button>Apri</button></form></div>'
    if selected:
        config = _settings(selected)
        favorites = config["favorites"] if isinstance(config["favorites"], list) else []
        rows = "".join(
            f'<tr><td><b>{_e(item.get("name"))}</b></td><td>{_e(item.get("room"))}</td><td>{_e(item.get("favorite"))}</td><td><code>{_e(item.get("command"))}</code></td><td><form class="inline" method="post" action="/control4-favorites/{_e(item.get("id"))}/test"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{selected.id}"><button>Prova</button></form> <form class="inline" method="post" action="/control4-favorites/{_e(item.get("id"))}/delete"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{selected.id}"><button class="danger">Elimina</button></form></td></tr>'
            for item in favorites
            if isinstance(item, dict)
        )
        body += f'''<div class="card"><h2>Connessione locale Control4</h2><form method="post" action="/control4-favorites/settings"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{selected.id}"><label class="field"><b>IP</b><input name="host" value="{_e(config["host"])}" placeholder="192.168.3.10" required></label><label class="field"><b>Porta</b><input name="port" type="number" min="1" max="65535" value="{_e(config["port"])}" required></label><button>Salva IP e porta</button></form></div><div class="card"><h2>Nuovo preferito ({len(favorites)}/{MAX_FAVORITES})</h2><form method="post"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="installation_id" value="{selected.id}"><label class="field"><b>Nome visualizzato</b><input name="name" maxlength="120" placeholder="Discoradio" required></label><label class="field"><b>Stanza</b><input name="room" maxlength="64" placeholder="ufficio_alex" required></label><label class="field"><b>Preferito</b><input name="favorite" maxlength="64" placeholder="radio_1" required></label><label class="field"><b>Comando finale (facoltativo)</b><input name="command" maxlength="128" placeholder="Generato: play_ufficio_alex_radio_1"></label><button>Crea preferito</button></form></div><table><thead><tr><th>Nome</th><th>Stanza</th><th>Preferito</th><th>Comando</th><th>Azioni</th></tr></thead><tbody>{rows or '<tr><td colspan="5">Nessun preferito configurato.</td></tr>'}</tbody></table>'''
    response = HTMLResponse(
        _layout("Preferiti musicali", body, context, csrf, "control4-favorites")
    )
    response.set_cookie(CSRF_COOKIE, csrf, httponly=True, secure=True, samesite="lax")
    return response


async def _form_context(
    request: Request, context: TenantContext, session: AsyncSession
) -> tuple[dict[str, list[str]], Installation]:
    values = await _multi_form(request)
    if not _valid_csrf(_one(values, "csrf_token"), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    return values, await _installation(session, context, UUID(_one(values, "installation_id")))


@router.post("/settings")
async def save_settings(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _lab()
    _admin(context)
    values, installation = await _form_context(request, context, session)
    host = _one(values, "host").strip()
    try:
        port = int(_one(values, "port"))
    except ValueError as error:
        raise HTTPException(422, "Porta non valida") from error
    if not _valid_host(host) or not 1 <= port <= 65535:
        raise HTTPException(422, "Usa un IP privato e una porta valida")
    config = _settings(installation)
    config.update({"host": host, "port": port})
    installation.control4_favorites_json = config
    await session.commit()
    return _back(installation.id, "success", "IP e porta Control4 salvati.")


@router.post("")
async def create(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _lab()
    _admin(context)
    values, installation = await _form_context(request, context, session)
    config = _settings(installation)
    favorites = list(config["favorites"])
    if len(favorites) >= MAX_FAVORITES:
        raise HTTPException(409, "Limite di 100 preferiti raggiunto")
    name, room, favorite = (_one(values, key).strip() for key in ("name", "room", "favorite"))
    command = _one(values, "command").strip() or f"play_{room}_{favorite}"
    if (
        not name
        or PART.fullmatch(room) is None
        or PART.fullmatch(favorite) is None
        or COMMAND.fullmatch(command) is None
        or ".." in command
        or "//" in command
    ):
        raise HTTPException(422, "Nome, stanza, preferito o comando non validi")
    if any(item.get("command") == command for item in favorites if isinstance(item, dict)):
        raise HTTPException(409, "Comando già presente")
    favorites.append(
        {"id": str(uuid4()), "name": name, "room": room, "favorite": favorite, "command": command}
    )
    config["favorites"] = favorites
    installation.control4_favorites_json = config
    await session.commit()
    return _back(installation.id, "success", "Preferito creato.")


def _find(config: dict[str, object], item_id: UUID) -> dict[str, object] | None:
    return next(
        (
            item
            for item in config.get("favorites", [])
            if isinstance(item, dict) and item.get("id") == str(item_id)
        ),
        None,
    )


@router.post("/{item_id}/test")
async def test(
    item_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _lab()
    _admin(context)
    _, installation = await _form_context(request, context, session)
    config = _settings(installation)
    item = _find(config, item_id)
    if item is None or not _valid_host(str(config["host"])):
        raise HTTPException(422, "Configura prima IP, porta e preferito")
    result = await sessions.dispatch(
        installation.id,
        uuid4(),
        "local:control4",
        {
            "operation": "control4_favorite",
            "host": config["host"],
            "port": config["port"],
            "command": item["command"],
        },
        8,
    )
    ok = result.status == "success"
    return _back(
        installation.id,
        "success" if ok else "error",
        "Comando inviato a Control4."
        if ok
        else f"Comando non riuscito: {result.error_code or result.status}",
    )


@router.post("/{item_id}/delete")
async def delete(
    item_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _lab()
    _admin(context)
    _, installation = await _form_context(request, context, session)
    config = _settings(installation)
    config["favorites"] = [
        item
        for item in config["favorites"]
        if not isinstance(item, dict) or item.get("id") != str(item_id)
    ]
    installation.control4_favorites_json = config
    await session.commit()
    return _back(installation.id, "success", "Preferito eliminato.")
