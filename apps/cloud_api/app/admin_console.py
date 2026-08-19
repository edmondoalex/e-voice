"""Server-rendered, tenant-scoped administration console."""

# HTML is deliberately kept inline so the console ships without a template runtime.
# ruff: noqa: E501

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import TenantContext
from .command_dispatch import CommandDispatchService, command_adapter
from .database import get_database_session
from .domain.enums import TenantRole
from .domain.models import (
    AuditEvent,
    Entity,
    EntityStateHistory,
    Installation,
    OperationalEvent,
)
from .evcp import LIVENESS_TIMEOUT_SECONDS, sessions
from .pairing_api import CSRF_COOKIE, _csrf, _form, _valid_csrf, identity_dependency
from .portal_auth import PortalIdentity

router = APIRouter(tags=["admin-console"])
session_dependency = Depends(get_database_session)
WRITE_ROLES = {
    TenantRole.OWNER,
    TenantRole.DEALER_ADMIN,
    TenantRole.INSTALLER,
    TenantRole.CUSTOMER_ADMIN,
}
PAGE_SIZE = 50
DOMAIN_OPERATIONS = {
    "light": {"power_on", "power_off", "set_brightness"},
    "switch": {"power_on", "power_off"},
    "cover": {"open", "close", "stop", "set_position"},
    "climate": {"set_target_temperature", "set_hvac_mode"},
    "fan": {"power_on", "power_off", "set_percentage"},
    "scene": {"activate"},
    "script": {"activate"},
    "button": {"press"},
}


async def _console_context(
    identity: Annotated[PortalIdentity | None, identity_dependency],
) -> TenantContext:
    if identity is None:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    if identity.context is None:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/pair/tenant"})
    return identity.context


console_context_dependency = Depends(_console_context)


def _e(value: object | None) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _nav_link(href: str, label: str, key: str, active: str) -> str:
    attributes = ' class="active" aria-current="page"' if key == active else ""
    return f'<a href="{href}"{attributes}>{label}</a>'


def _layout(title: str, body: str, context: TenantContext, csrf: str, active: str) -> str:
    navigation = "".join(
        (
            _nav_link("/dashboard", "Dashboard", "dashboard", active),
            _nav_link("/installations", "Impianti", "installations", active),
            _nav_link("/activity", "Attività", "activity", active),
            _nav_link("/system", "Sistema", "system", active),
            _nav_link("/pair", "Collega a e-Control", "pair", active),
        )
    )
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · Ekonex Cloud Voice</title><style>
:root{{--ink:#17202a;--muted:#667085;--blue:#1769e0;--bg:#f4f6f9;--card:#fff;--bad:#b42318;--ok:#067647}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px system-ui,sans-serif}}
aside{{position:fixed;inset:0 auto 0 0;width:230px;background:#101828;color:white;padding:24px}}
aside a{{display:block;color:#d0d5dd;text-decoration:none;padding:10px 12px;border-radius:7px}}aside a:hover,aside a:focus-visible{{background:#1d2939;color:white}}aside a.active{{background:#344054;color:white;font-weight:700}}main{{margin-left:230px;padding:28px;max-width:1400px}}
.brand-logo{{display:block;width:min(100%,160px);height:auto;aspect-ratio:1/1;object-fit:contain;margin:0 auto 20px;border-radius:10px;background:#050505}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}}
.card,table{{background:var(--card);border-radius:10px;box-shadow:0 1px 3px #10182818}}.card{{padding:18px}}table{{width:100%;border-collapse:collapse;margin-top:16px}}
th,td{{padding:12px;text-align:left;border-bottom:1px solid #eaecf0}}input,select,button{{padding:9px;border:1px solid #d0d5dd;border-radius:7px}}
button,.button{{background:var(--blue);color:white;border:0;text-decoration:none;display:inline-block;padding:9px 12px;border-radius:7px}}
.ok{{color:var(--ok)}}.bad{{color:var(--bad)}}.muted{{color:var(--muted)}}form.inline{{display:inline}}@media(max-width:720px){{aside{{position:static;width:auto}}main{{margin:0;padding:16px}}table{{display:block;overflow:auto}}}}
</style></head><body><aside><img class="brand-logo" src="/static/ekonex-cloud-voice.png" width="1254" height="1254" alt="Ekonex Cloud Voice">
<nav aria-label="Navigazione principale">{navigation}</nav>
<form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><button>Esci</button></form>
</aside><main><p class="muted">Tenant: {_e(context.tenant_id)}</p><h1>{_e(title)}</h1>{body}</main></body></html>"""


def _admin(context: TenantContext) -> None:
    if context.role not in WRITE_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Permessi insufficienti")


async def _installation(
    session: AsyncSession, context: TenantContext, installation_id: UUID
) -> Installation:
    item = await session.scalar(
        select(Installation).where(
            Installation.id == installation_id, Installation.tenant_id == context.tenant_id
        )
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Installazione non trovata")
    return item


def _online(item: Installation) -> bool:
    return bool(
        item.last_seen_at
        and item.last_seen_at >= datetime.now(UTC) - timedelta(seconds=LIVENESS_TIMEOUT_SECONDS)
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _admin(context)
    items = list(
        (
            await session.scalars(
                select(Installation)
                .where(Installation.tenant_id == context.tenant_id)
                .order_by(Installation.name)
            )
        ).all()
    )
    entity_count = (
        await session.scalar(
            select(func.count(Entity.id))
            .join(Installation)
            .where(Installation.tenant_id == context.tenant_id, Entity.deleted_at.is_(None))
        )
        or 0
    )
    rows = "".join(
        f'<tr><td><a href="/installations/{item.id}">{_e(item.name)}</a></td><td class="{"ok" if _online(item) else "bad"}">{"online" if _online(item) else "offline"}</td><td>{_e(item.ha_version)}</td><td>{_e(item.connector_version)}</td><td>{_e(item.last_seen_at)}</td></tr>'
        for item in items
    )
    csrf = _csrf(context)
    body = f'<div class="cards"><div class="card"><b>{len(items)}</b><br>Installazioni</div><div class="card"><b>{entity_count}</b><br>Entità esposte</div><div class="card"><b>{sum(_online(i) for i in items)}</b><br>Connesse</div></div><table><thead><tr><th>Installazione</th><th>Stato</th><th>e-Control</th><th>Connector</th><th>Ultimo contatto</th></tr></thead><tbody>{rows or "<tr><td colspan=5>Nessuna installazione</td></tr>"}</tbody></table>'
    response = HTMLResponse(_layout("Dashboard", body, context, csrf, "dashboard"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


@router.get("/installations", response_class=HTMLResponse)
async def installations_page(
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _admin(context)
    result = await session.execute(
        select(Installation, func.count(Entity.id))
        .outerjoin(
            Entity,
            (Entity.installation_id == Installation.id) & Entity.deleted_at.is_(None),
        )
        .where(Installation.tenant_id == context.tenant_id)
        .group_by(Installation.id)
        .order_by(Installation.name)
    )
    rows = "".join(
        f'<tr><td><a href="/installations/{item.id}">{_e(item.name)}</a></td><td class="{"ok" if _online(item) else "bad"}">{"online" if _online(item) else "offline"}</td><td>{_e(item.ha_version)}</td><td>{_e(item.connector_version)}</td><td>{entity_count}</td><td>{_e(item.last_seen_at)}</td></tr>'
        for item, entity_count in result.all()
    )
    csrf = _csrf(context)
    body = f"<table><thead><tr><th>Nome</th><th>Stato</th><th>Versione e-Control</th><th>Versione Connector</th><th>Entità esposte</th><th>Ultimo contatto</th></tr></thead><tbody>{rows or '<tr><td colspan=6>Nessun impianto</td></tr>'}</tbody></table>"
    response = HTMLResponse(_layout("Impianti", body, context, csrf, "installations"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


@router.get("/installations/{installation_id}", response_class=HTMLResponse)
async def installation_detail(
    installation_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _admin(context)
    item = await _installation(session, context, installation_id)
    q, domain, area = (request.query_params.get(key, "").strip() for key in ("q", "domain", "area"))
    page = max(1, int(request.query_params.get("page", "1")))
    query = select(Entity).where(Entity.installation_id == item.id)
    if q:
        query = query.where(
            or_(Entity.friendly_name.ilike(f"%{q}%"), Entity.ha_entity_id.ilike(f"%{q}%"))
        )
    if domain:
        query = query.where(Entity.ha_domain == domain)
    if area:
        query = query.where(Entity.area_name == area)
    entities = list(
        (
            await session.scalars(
                query.order_by(Entity.friendly_name, Entity.ha_entity_id)
                .offset((page - 1) * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        ).all()
    )
    csrf = _csrf(context)
    rows = "".join(_entity_row(item, entity, csrf) for entity in entities)
    body = f'<div class="cards"><div class="card"><b>{"online" if _online(item) else "offline"}</b><br>Connessione</div><div class="card"><b>{_e(item.sync_revision)}</b><br>Revisione inventario</div><div class="card"><b>{_e(item.inventory_synced_at)}</b><br>Ultima sincronizzazione</div></div><form method="get"><input name="q" placeholder="Cerca" value="{_e(q)}"><input name="domain" placeholder="Dominio" value="{_e(domain)}"><input name="area" placeholder="Area" value="{_e(area)}"><button>Filtra</button></form><table><thead><tr><th>Entità</th><th>Dominio/area</th><th>Stato</th><th>Comando sicuro</th></tr></thead><tbody>{rows or "<tr><td colspan=4>Nessuna entità</td></tr>"}</tbody></table>'
    response = HTMLResponse(_layout(item.name, body, context, csrf, "installations"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


def _entity_row(installation: Installation, entity: Entity, csrf: str) -> str:
    operations = sorted(DOMAIN_OPERATIONS.get(entity.ha_domain, ()))
    controls = ""
    if entity.deleted_at is None and entity.available and entity.ha_registry_id and operations:
        options = "".join(f'<option value="{op}">{op}</option>' for op in operations)
        controls = f'<form method="post" action="/installations/{installation.id}/commands" onsubmit="this.querySelector(\'button\').textContent=\'Invio…\'"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="entity_id" value="{entity.id}"><select name="operation">{options}</select><input name="value" size="5" placeholder="valore"><button>Invia</button></form>'
    label = entity.friendly_name or entity.ha_entity_id
    lifecycle = "rimossa" if entity.deleted_at else (entity.state or "—")
    return f'<tr><td><b>{_e(label)}</b><br><span class="muted">{_e(entity.ha_entity_id)}</span></td><td>{_e(entity.ha_domain)} / {_e(entity.area_name)}</td><td>{_e(lifecycle)} · {"disponibile" if entity.available else "non disponibile"}</td><td>{controls or "—"}</td></tr>'


def _command_data(operation: str, value: str) -> dict[str, object]:
    data: dict[str, object] = {"operation": operation}
    if operation == "set_brightness":
        data["brightness"] = int(value)
    elif operation == "set_position":
        data["position"] = int(value)
    elif operation == "set_target_temperature":
        data["temperature"] = float(value)
    elif operation == "set_hvac_mode":
        data["hvac_mode"] = value
    elif operation == "set_percentage":
        data["percentage"] = int(value)
    return data


@router.post("/installations/{installation_id}/commands", response_class=HTMLResponse)
async def send_command(
    installation_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _admin(context)
    installation = await _installation(session, context, installation_id)
    values = await _form(request)
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    try:
        entity_id = UUID(values.get("entity_id", ""))
        entity = await session.scalar(
            select(Entity).where(
                Entity.id == entity_id,
                Entity.installation_id == installation.id,
                Entity.deleted_at.is_(None),
            )
        )
        if entity is None or entity.ha_registry_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Entità non trovata")
        if values.get("operation", "") not in DOMAIN_OPERATIONS.get(entity.ha_domain, set()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Comando non consentito")
        command = command_adapter.validate_python(
            _command_data(values.get("operation", ""), values.get("value", ""))
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Comando non valido") from error
    request_id = uuid4()
    session.add(
        AuditEvent(
            tenant_id=context.tenant_id,
            installation_id=installation.id,
            user_id=context.user_id,
            source="admin_console",
            event_type="command_sent",
            request_id=str(request_id),
            payload_redacted_json={"entity_id": str(entity.id), "operation": command.operation},
            result="pending",
        )
    )
    await session.commit()
    outcome = await CommandDispatchService(session, sessions).dispatch(
        installation.id, entity.ha_registry_id, command, command_id=request_id
    )
    session.add(
        AuditEvent(
            tenant_id=context.tenant_id,
            installation_id=installation.id,
            user_id=context.user_id,
            source="admin_console",
            event_type="command_result",
            request_id=str(request_id),
            payload_redacted_json={"entity_id": str(entity.id), "operation": command.operation},
            result=outcome.status,
        )
    )
    await session.commit()
    csrf = _csrf(context)
    body = f'<div class="card"><b>Esito: {_e(outcome.status)}</b><p>Il comando è stato completato dal dispatcher EVCP; nessun esito è simulato.</p><a class="button" href="/installations/{installation.id}">Torna all’installazione</a></div>'
    response = HTMLResponse(_layout("Esito comando", body, context, csrf, "installations"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


@router.get("/activity", response_class=HTMLResponse)
async def activity(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _admin(context)
    installation_filter = request.query_params.get("installation_id", "")
    installation_id: UUID | None = None
    if installation_filter:
        try:
            installation_id = UUID(installation_filter)
        except ValueError as error:
            raise HTTPException(422, "Filtro non valido") from error
        await _installation(session, context, installation_id)
    aq = select(AuditEvent).where(AuditEvent.tenant_id == context.tenant_id)
    oq = select(OperationalEvent).where(OperationalEvent.tenant_id == context.tenant_id)
    if installation_id:
        aq, oq = (
            aq.where(AuditEvent.installation_id == installation_id),
            oq.where(OperationalEvent.installation_id == installation_id),
        )
    outcome = request.query_params.get("outcome", "")
    if outcome:
        aq, oq = (
            aq.where(AuditEvent.result == outcome),
            oq.where(OperationalEvent.outcome == outcome),
        )
    event_type = request.query_params.get("event_type", "")
    if event_type:
        aq = aq.where(AuditEvent.event_type == event_type)
        oq = oq.where(OperationalEvent.event_type == event_type)
    entity_filter = request.query_params.get("entity_id", "")
    if entity_filter:
        try:
            entity_id = UUID(entity_filter)
        except ValueError as error:
            raise HTTPException(422, "Filtro non valido") from error
        owned = await session.scalar(
            select(Entity.id)
            .join(Installation)
            .where(Entity.id == entity_id, Installation.tenant_id == context.tenant_id)
        )
        if owned is None:
            raise HTTPException(404, "Entità non trovata")
        oq = oq.where(OperationalEvent.entity_id == entity_id)
        aq = aq.where(AuditEvent.payload_redacted_json["entity_id"].as_string() == str(entity_id))
    date_from, date_to = (request.query_params.get(key, "") for key in ("from", "to"))
    try:
        if date_from:
            start = datetime.fromisoformat(date_from).replace(tzinfo=UTC)
            aq, oq = (
                aq.where(AuditEvent.created_at >= start),
                oq.where(OperationalEvent.created_at >= start),
            )
        if date_to:
            end = datetime.fromisoformat(date_to).replace(tzinfo=UTC) + timedelta(days=1)
            aq, oq = (
                aq.where(AuditEvent.created_at < end),
                oq.where(OperationalEvent.created_at < end),
            )
    except ValueError as error:
        raise HTTPException(422, "Data non valida") from error
    audits = list(
        (await session.scalars(aq.order_by(AuditEvent.created_at.desc()).limit(200))).all()
    )
    operations = list(
        (await session.scalars(oq.order_by(OperationalEvent.created_at.desc()).limit(200))).all()
    )
    all_events = sorted(
        [
            *((e.created_at, e.event_type, e.source, e.result, e.installation_id) for e in audits),
            *(
                (e.created_at, e.event_type, e.source, e.outcome, e.installation_id)
                for e in operations
            ),
        ],
        reverse=True,
    )
    page = max(1, int(request.query_params.get("page", "1")))
    events = all_events[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    rows = "".join(
        f"<tr><td>{_e(at)}</td><td>{_e(kind)}</td><td>{_e(source)}</td><td>{_e(result)}</td><td>{_e(iid)}</td></tr>"
        for at, kind, source, result, iid in events
    )
    csrf = _csrf(context)
    body = f'<form method="get"><input name="installation_id" placeholder="ID installazione" value="{_e(installation_filter)}"><input name="outcome" placeholder="Esito" value="{_e(outcome)}"><button>Filtra</button></form><table><thead><tr><th>Data</th><th>Evento</th><th>Fonte</th><th>Esito</th><th>Installazione</th></tr></thead><tbody>{rows or "<tr><td colspan=5>Nessuna attività</td></tr>"}</tbody></table>'
    response = HTMLResponse(_layout("Attività", body, context, csrf, "activity"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


@router.get("/system", response_class=HTMLResponse)
async def system_stats(
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _admin(context)
    installations = (
        await session.scalar(
            select(func.count(Installation.id)).where(Installation.tenant_id == context.tenant_id)
        )
        or 0
    )
    entities = (
        await session.scalar(
            select(func.count(Entity.id))
            .join(Installation)
            .where(Installation.tenant_id == context.tenant_id)
        )
        or 0
    )
    history = (
        await session.scalar(
            select(func.count(EntityStateHistory.id)).where(
                EntityStateHistory.tenant_id == context.tenant_id
            )
        )
        or 0
    )
    audit = (
        await session.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.tenant_id == context.tenant_id)
        )
        or 0
    )
    database_size: int | str = "n/d"
    if session.get_bind().dialect.name == "postgresql":
        database_size = int(
            await session.scalar(select(func.pg_database_size(func.current_database()))) or 0
        )
    csrf = _csrf(context)
    body = f'<div class="cards"><div class="card"><b>{installations}</b><br>Installazioni</div><div class="card"><b>{entities}</b><br>Entità</div><div class="card"><b>{history}</b><br>Campioni storico</div><div class="card"><b>{audit}</b><br>Eventi audit</div><div class="card"><b>{_e(database_size)}</b><br>Dimensione DB (byte)</div></div><p>La pulizia retention è eseguita con <code>python -m apps.cloud_api.app.cleanup</code>.</p>'
    response = HTMLResponse(_layout("Sistema", body, context, csrf, "system"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response
