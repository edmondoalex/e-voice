"""Server-rendered, tenant-scoped administration console."""

# HTML is deliberately kept inline so the console ships without a template runtime.
# ruff: noqa: E501

from __future__ import annotations

import csv
import html
import io
import json
import math
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, TypedDict
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .alexa_events import reconcile_discovery_safely
from .auth import TenantContext
from .command_dispatch import CommandDispatchService, command_adapter
from .config import get_settings
from .connector_compatibility import (
    MINIMUM_SUPPORTED_CONNECTOR_VERSION,
    RECOMMENDED_CONNECTOR_VERSION,
    REQUIRED_EVCP_PROTOCOL_VERSION,
    ConnectorCompatibilityStatus,
)
from .alexa_device_types import allowed_alexa_device_types, validate_alexa_device_type
from .cover_modes import COVER_STOP, effective_cover_mode, validate_cover_mode
from .database import get_database_session
from .domain.enums import TenantRole
from .domain.models import (
    AlexaAccountLink,
    AlexaDiscoveryDelivery,
    AlexaDiscoverySnapshot,
    AuditEvent,
    Entity,
    EntityStateHistory,
    Installation,
    MaintenanceRun,
    OperationalEvent,
)
from .entity_icons import entity_icon_svg
from .entity_names import (
    all_voice_names,
    clean_optional_name,
    clean_voice_aliases,
    effective_display_name,
    effective_voice_name,
    voice_collisions,
)
from .evcp import LIVENESS_TIMEOUT_SECONDS, sessions
from .maintenance import latest_cleanup, next_cleanup_at
from .pairing_api import CSRF_COOKIE, _csrf, _form, _valid_csrf, identity_dependency
from .portal_auth import PortalIdentity


class ActivityRow(TypedDict):
    at: datetime
    kind: str
    source: str
    result: str
    installation_id: UUID | None
    request_id: str | None
    detail: dict[str, Any]


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
:root{{--ink:#17202a;--muted:#667085;--blue:#1769e0;--bg:#f4f6f9;--card:#fff;--bad:#b42318;--ok:#067647;--off:#667085;--removed:#dc6803}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px system-ui,sans-serif}}
aside{{position:fixed;inset:0 auto 0 0;width:230px;background:#101828;color:white;padding:24px}}
aside a{{display:block;color:#d0d5dd;text-decoration:none;padding:10px 12px;border-radius:7px}}aside a:hover,aside a:focus-visible{{background:#1d2939;color:white}}aside a.active{{background:#344054;color:white;font-weight:700}}main{{margin-left:230px;padding:28px;max-width:1400px}}
.brand-logo{{display:block;width:min(100%,160px);height:auto;aspect-ratio:1/1;object-fit:contain;margin:0 auto 20px;border-radius:10px;background:#050505}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}}
.card,table{{background:var(--card);border-radius:10px;box-shadow:0 1px 3px #10182818}}.card{{padding:18px}}table{{width:100%;border-collapse:collapse;margin-top:16px}}
th,td{{padding:12px;text-align:left;border-bottom:1px solid #eaecf0}}input,select,textarea,button{{padding:9px;border:1px solid #d0d5dd;border-radius:7px;font:inherit}}textarea{{width:100%;min-height:120px}}
button,.button{{background:var(--blue);color:white;border:0;text-decoration:none;display:inline-block;padding:9px 12px;border-radius:7px}}
.ok{{color:var(--ok)}}.bad{{color:var(--bad)}}.warn{{color:var(--removed)}}.muted{{color:var(--muted)}}.badge{{display:inline-block;margin-left:6px;padding:2px 7px;border-radius:999px;background:#e8f0fe;color:#174ea6;font-size:12px;font-weight:700}}.compat-badge{{display:inline-block;padding:4px 9px;border-radius:999px;font-size:12px;font-weight:800}}.compat-ok{{background:#dcfae6;color:var(--ok)}}.compat-update{{background:#fef0c7;color:#93370d}}.compat-bad{{background:#fee4e2;color:var(--bad)}}.compat-offline{{background:#eaecf0;color:var(--off)}}.compat-alert{{border:2px solid var(--bad);background:#fff5f4}}.global-warning{{border-left:6px solid var(--bad);background:#fff5f4;margin-bottom:16px}}form.inline{{display:inline}}.field{{display:block;margin:16px 0}}.field input{{display:block;width:100%;margin-top:6px}}.actions,.direct-controls{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}button.danger{{background:var(--bad)}}.command-button{{background:#e4e7ec;color:var(--ink)}}.command-button.active-on{{background:var(--ok);color:white;font-weight:700}}.command-button.active-off{{background:var(--off);color:white;font-weight:700}}button:disabled,input:disabled{{opacity:.45;cursor:not-allowed}}.entity-summary{{display:flex;align-items:flex-start;gap:10px;min-width:250px}}.entity-icon{{flex:0 0 auto;fill:var(--blue)}}.entity-meta{{line-height:1.45}}.voice-label{{font-size:12px;color:var(--blue);font-weight:700;text-transform:uppercase}}.status-dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;background:var(--off)}}.status-dot.state-on{{background:var(--ok)}}.status-dot.state-off{{background:var(--off)}}.status-dot.state-unavailable{{background:var(--bad)}}.status-dot.state-removed{{background:var(--removed)}}.level-control input{{width:110px;padding:0}}.level-value{{min-width:38px;font-variant-numeric:tabular-nums}}.command-feedback{{flex-basis:100%;min-height:20px;font-size:13px}}tr.state-on td:first-child{{box-shadow:inset 3px 0 var(--ok)}}@media(max-width:720px){{aside{{position:static;width:auto}}main{{margin:0;padding:16px}}table{{display:block;overflow:auto}}}}
</style></head><body><aside><img class="brand-logo" src="/static/ekonex-cloud-voice.png" width="1254" height="1254" alt="Ekonex Cloud Voice">
<nav aria-label="Navigazione principale">{navigation}</nav>
<form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><button>Esci</button></form>
</aside><main><p class="muted">Tenant: {_e(context.tenant_id)}</p><h1>{_e(title)}</h1>{body}</main><script>
let csrfRefreshPromise = null;
async function renewCommandCsrf() {{
  if (!csrfRefreshPromise) {{
    csrfRefreshPromise = fetch('/admin/csrf', {{headers: {{Accept: 'application/json'}}, credentials: 'same-origin'}})
      .then(async (response) => {{
        const payload = await response.json();
        if (!response.ok || !payload.csrf_token) throw new Error('Sessione scaduta');
        document.querySelectorAll('input[name="csrf_token"]').forEach((input) => input.value = payload.csrf_token);
        return payload.csrf_token;
      }})
      .finally(() => {{ csrfRefreshPromise = null; }});
  }}
  return csrfRefreshPromise;
}}
async function postEntityCommand(form, retried = false) {{
  const response = await fetch(form.action, {{method: 'POST', body: new URLSearchParams(new FormData(form)), headers: {{Accept: 'application/json'}}, credentials: 'same-origin'}});
  const payload = await response.json();
  if (response.status === 403 && payload.code === 'csrf_invalid' && !retried) {{
    await renewCommandCsrf();
    return postEntityCommand(form, true);
  }}
  return {{response, payload}};
}}
document.querySelectorAll('.entity-command').forEach((form) => {{
  const slider = form.querySelector('input[type="range"]');
  const value = form.querySelector('.level-value');
  if (slider && value) slider.addEventListener('input', () => value.textContent = `${{slider.value}}%`);
  form.addEventListener('submit', async (event) => {{
    event.preventDefault();
    const row = form.closest('[data-entity-row]');
    const feedback = row.querySelector('.command-feedback');
    const button = form.querySelector('button');
    feedback.className = 'command-feedback muted';
    feedback.textContent = 'Invio...';
    button.disabled = true;
    try {{
      const {{response, payload}} = await postEntityCommand(form);
      if (!response.ok || !payload.ok) throw new Error(payload.detail || payload.message || 'Comando non riuscito');
      feedback.className = 'command-feedback ok';
      feedback.textContent = 'Comando eseguito';
      if (Object.hasOwn(payload, 'value')) feedback.textContent = `Comando eseguito: ${{payload.value}}`;
      if (payload.state === 'on' || payload.state === 'off') {{
        row.classList.remove('state-on', 'state-off');
        row.classList.add(`state-${{payload.state}}`);
        row.querySelector('.entity-state').textContent = payload.state;
        row.querySelector('.status-dot').className = `status-dot state-${{payload.state}}`;
        row.querySelectorAll('[data-power]').forEach((item) => {{
          item.classList.toggle('active-on', payload.state === 'on' && item.dataset.power === 'on');
          item.classList.toggle('active-off', payload.state === 'off' && item.dataset.power === 'off');
          item.setAttribute('aria-pressed', String(item.dataset.power === payload.state));
        }});
      }}
    }} catch (error) {{
      feedback.className = 'command-feedback bad';
      feedback.textContent = error.message || 'Comando non riuscito';
    }} finally {{ button.disabled = false; }}
  }});
}});
</script></body></html>"""


@router.get("/admin/csrf", response_class=JSONResponse)
async def renew_admin_csrf(
    context: Annotated[TenantContext, console_context_dependency],
) -> JSONResponse:
    """Issue a fresh CSRF pair for authenticated administrative AJAX calls."""
    _admin(context)
    token = _csrf(context)
    response = JSONResponse(
        {"csrf_token": token},
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        CSRF_COOKIE,
        token,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=1800,
    )
    return response


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


async def _entity(session: AsyncSession, installation: Installation, entity_id: UUID) -> Entity:
    item = await session.scalar(
        select(Entity).where(Entity.id == entity_id, Entity.installation_id == installation.id)
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entità non trovata")
    return item


def _online(item: Installation) -> bool:
    return bool(
        item.last_seen_at
        and item.last_seen_at >= datetime.now(UTC) - timedelta(seconds=LIVENESS_TIMEOUT_SECONDS)
    )


def _compatibility_status(item: Installation) -> ConnectorCompatibilityStatus:
    if not _online(item):
        return ConnectorCompatibilityStatus.UNKNOWN_OFFLINE
    if item.connector_compatibility_status is None:
        return ConnectorCompatibilityStatus.UNKNOWN_OFFLINE
    try:
        return ConnectorCompatibilityStatus(item.connector_compatibility_status)
    except (TypeError, ValueError):
        return ConnectorCompatibilityStatus.UNKNOWN_OFFLINE


def _compatibility_badge(item: Installation) -> str:
    status_value = _compatibility_status(item)
    css = {
        ConnectorCompatibilityStatus.OK: "compat-ok",
        ConnectorCompatibilityStatus.UPDATE_AVAILABLE: "compat-update",
        ConnectorCompatibilityStatus.INCOMPATIBLE: "compat-bad",
        ConnectorCompatibilityStatus.UNKNOWN_OFFLINE: "compat-offline",
    }[status_value]
    return f'<span class="compat-badge {css}">{_e(status_value.value)}</span>'


def _connector_compatibility_card(item: Installation) -> str:
    status_value = _compatibility_status(item)
    alert = " compat-alert" if status_value is ConnectorCompatibilityStatus.INCOMPATIBLE else ""
    warning = (
        "<p><b>I comandi possono non funzionare.</b></p>"
        if status_value is ConnectorCompatibilityStatus.INCOMPATIBLE
        else ""
    )
    return f"""<section class="card{alert}"><h2>Compatibilità Connector Home Assistant</h2><p><b>Connector Home Assistant:</b> {_e(item.connector_version or "—")}</p><p><b>Versione richiesta:</b> &gt;= {_e(MINIMUM_SUPPORTED_CONNECTOR_VERSION)}</p><p><b>Versione raccomandata:</b> {_e(RECOMMENDED_CONNECTOR_VERSION)}</p><p><b>Protocollo EVCP:</b> {_e(item.connector_protocol_version or "—")} (richiesto: {_e(REQUIRED_EVCP_PROTOCOL_VERSION)})</p><p><b>Stato:</b> {_compatibility_badge(item)}</p><p class="muted">Motivo: {_e(item.connector_compatibility_reason or "connector_metadata_missing")}</p>{warning}</section>"""


async def _database_size_mb(session: AsyncSession) -> float | None:
    """Read PostgreSQL's authoritative database size and convert bytes to decimal MB."""
    if session.get_bind().dialect.name != "postgresql":
        return None
    database_size_bytes = int(
        await session.scalar(select(func.pg_database_size(func.current_database()))) or 0
    )
    return database_size_bytes / 1_000_000


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
        f'<tr><td><a href="/installations/{item.id}">{_e(item.name)}</a></td><td class="{"ok" if _online(item) else "bad"}">{"online" if _online(item) else "offline"}</td><td>{_e(item.ha_version)}</td><td>{_e(item.connector_version)}</td><td>{_compatibility_badge(item)}</td><td>{_e(item.last_seen_at)}</td></tr>'
        for item in items
    )
    incompatible_count = sum(
        _compatibility_status(item) is ConnectorCompatibilityStatus.INCOMPATIBLE for item in items
    )
    global_warning = (
        f'<div class="card global-warning"><b>Attenzione: {incompatible_count} Connector incompatibile/i</b><p>I comandi possono non funzionare. Apri Impianti o Sistema per i dettagli.</p></div>'
        if incompatible_count
        else ""
    )
    csrf = _csrf(context)
    body = f'{global_warning}<div class="cards"><div class="card"><b>{len(items)}</b><br>Installazioni</div><div class="card"><b>{entity_count}</b><br>Entità esposte</div><div class="card"><b>{sum(_online(i) for i in items)}</b><br>Connesse</div></div><table><thead><tr><th>Installazione</th><th>Stato</th><th>e-Control</th><th>Connector</th><th>Compatibilità</th><th>Ultimo contatto</th></tr></thead><tbody>{rows or "<tr><td colspan=6>Nessuna installazione</td></tr>"}</tbody></table>'
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
        f'<tr><td><a href="/installations/{item.id}">{_e(item.name)}</a></td><td class="{"ok" if _online(item) else "bad"}">{"online" if _online(item) else "offline"}</td><td>{_e(item.ha_version)}</td><td>{_e(item.connector_version)}</td><td>{_compatibility_badge(item)}</td><td>{entity_count}</td><td>{_e(item.last_seen_at)}</td></tr>'
        for item, entity_count in result.all()
    )
    csrf = _csrf(context)
    body = f"<table><thead><tr><th>Nome</th><th>Stato</th><th>Versione e-Control</th><th>Versione Connector</th><th>Compatibilità</th><th>Entità esposte</th><th>Ultimo contatto</th></tr></thead><tbody>{rows or '<tr><td colspan=7>Nessun impianto</td></tr>'}</tbody></table>"
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
    query = select(Entity).where(
        Entity.installation_id == item.id,
        Entity.deleted_at.is_(None),
    )
    if q:
        query = query.where(
            or_(
                Entity.display_name.ilike(f"%{q}%"),
                Entity.voice_name.ilike(f"%{q}%"),
                Entity.friendly_name.ilike(f"%{q}%"),
                Entity.ha_entity_id.ilike(f"%{q}%"),
            )
        )
    if domain:
        query = query.where(Entity.ha_domain == domain)
    if area:
        query = query.where(Entity.area_name == area)
    entities = list(
        (
            await session.scalars(
                query.order_by(Entity.display_name, Entity.friendly_name, Entity.ha_entity_id)
                .offset((page - 1) * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        ).all()
    )
    discovery = await session.scalar(
        select(AlexaDiscoverySnapshot).where(
            AlexaDiscoverySnapshot.tenant_id == context.tenant_id,
            AlexaDiscoverySnapshot.installation_id == item.id,
        )
    )
    proactive_events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.tenant_id == context.tenant_id,
                    AuditEvent.installation_id == item.id,
                    AuditEvent.event_type.in_(
                        ["alexa.discovery.add_or_update", "alexa.discovery.delete"]
                    ),
                )
                .order_by(AuditEvent.created_at.desc())
                .limit(100)
            )
        ).all()
    )
    current_result = await session.execute(
        select(AlexaDiscoveryDelivery, Entity)
        .join(AlexaAccountLink, AlexaAccountLink.id == AlexaDiscoveryDelivery.link_id)
        .outerjoin(Entity, Entity.id == AlexaDiscoveryDelivery.entity_id)
        .where(
            AlexaAccountLink.tenant_id == context.tenant_id,
            AlexaAccountLink.status == "active",
            AlexaDiscoveryDelivery.installation_id == item.id,
            AlexaDiscoveryDelivery.removed_at.is_(None),
        )
    )
    current_alexa: dict[str, dict[str, object]] = {}
    for delivery, entity in current_result.all():
        current_alexa.setdefault(
            delivery.alexa_endpoint_id,
            {
                "endpoint_id": delivery.alexa_endpoint_id,
                "voice_name": effective_voice_name(entity) if entity is not None else "—",
                "domain": entity.ha_domain if entity is not None else "—",
            },
        )
    csrf = _csrf(context)
    rows = "".join(_entity_row(item, entity, csrf) for entity in entities)
    resync_status = request.query_params.get("alexa_resync", "")
    resync_count = request.query_params.get("sent", "0")
    resync_notice = (
        f'<p class="ok">Risincronizzazione Alexa completata: {_e(resync_count)} endpoint inviati.</p>'
        if resync_status == "success"
        else '<p class="bad">Risincronizzazione Alexa non riuscita.</p>'
        if resync_status == "error"
        else ""
    )
    resync_form = f'<form method="post" action="/installations/{item.id}/alexa/resync" class="actions"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><button>Risincronizza Alexa</button></form>'
    body = f'<div class="cards"><div class="card"><b>{"online" if _online(item) else "offline"}</b><br>Connessione</div><div class="card"><b>{_e(item.sync_revision)}</b><br>Revisione inventario</div><div class="card"><b>{_e(item.inventory_synced_at)}</b><br>Ultima sincronizzazione</div></div>{_connector_compatibility_card(item)}{resync_notice}{resync_form}{_alexa_discovery_section(discovery, proactive_events, list(current_alexa.values()))}<form method="get"><input name="q" placeholder="Cerca" value="{_e(q)}"><input name="domain" placeholder="Dominio" value="{_e(domain)}"><input name="area" placeholder="Area" value="{_e(area)}"><button>Filtra</button></form><table><thead><tr><th>Entità</th><th>Dominio/area</th><th>Stato</th><th>Comandi diretti</th></tr></thead><tbody>{rows or "<tr><td colspan=4>Nessuna entità</td></tr>"}</tbody></table>'
    response = HTMLResponse(_layout(item.name, body, context, csrf, "installations"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


@router.post("/installations/{installation_id}/alexa/resync", response_class=RedirectResponse)
async def resync_alexa_discovery(
    installation_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    """Force a tenant-scoped proactive Discovery refresh for one installation."""
    _admin(context)
    installation = await _installation(session, context, installation_id)
    values = await _form(request)
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    sent = await reconcile_discovery_safely(session, installation, force=True)
    succeeded = sent is not None
    session.add(
        AuditEvent(
            tenant_id=context.tenant_id,
            installation_id=installation.id,
            user_id=context.user_id,
            source="admin_console",
            event_type="alexa.discovery.resync_requested",
            payload_redacted_json={"sent_endpoint_count": sent or 0},
            result="success" if succeeded else "error",
        )
    )
    await session.commit()
    outcome = "success" if succeeded else "error"
    return RedirectResponse(
        f"/installations/{installation.id}?alexa_resync={outcome}&sent={sent or 0}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _alexa_discovery_section(
    snapshot: AlexaDiscoverySnapshot | None,
    proactive_events: list[AuditEvent],
    current_endpoints: list[dict[str, object]],
) -> str:
    latest: dict[str, AuditEvent] = {}
    for event in proactive_events:
        latest.setdefault(event.event_type, event)

    activity_candidates: list[tuple[datetime, str, str | None]] = []
    if snapshot is not None:
        activity_candidates.append((snapshot.discovered_at, "Discovery completa", None))
    for event_type, label in (
        ("alexa.discovery.add_or_update", "AddOrUpdateReport"),
        ("alexa.discovery.delete", "DeleteReport"),
    ):
        activity_event = latest.get(event_type)
        if activity_event is not None:
            activity_candidates.append((activity_event.created_at, label, activity_event.result))
    if activity_candidates:
        activity_at, activity_type, activity_result = max(
            activity_candidates, key=lambda item: item[0]
        )
        result_suffix = f" · {_e(activity_result)}" if activity_result is not None else ""
        latest_activity = f"<p><b>Ultima attività Alexa:</b> {_e(activity_at.strftime('%d/%m/%Y %H:%M'))} · {_e(activity_type)}{result_suffix}</p>"
    else:
        latest_activity = "<p><b>Ultima attività Alexa:</b> Nessuna attività Alexa registrata</p>"

    def report_line(event_type: str, label: str) -> str:
        event = latest.get(event_type)
        if event is None:
            return f"<p>{label}: —</p>"
        endpoint_value = event.payload_redacted_json.get("endpoint_id", "—")
        timestamp = event.created_at.strftime("%d/%m/%Y %H:%M")
        return f"<p>{label}: {_e(timestamp)} · endpoint {_e(endpoint_value)} · esito {_e(event.result)}</p>"

    reports = report_line(
        "alexa.discovery.add_or_update", "Ultimo AddOrUpdateReport"
    ) + report_line("alexa.discovery.delete", "Ultimo DeleteReport")
    current_rows = "".join(
        f'<li><b>{_e(endpoint.get("voice_name"))}</b><br><span class="muted">{_e(endpoint.get("endpoint_id"))} · {_e(endpoint.get("domain"))}</span></li>'
        for endpoint in current_endpoints
    )
    current_inventory = f"<h3>Dispositivi attualmente presenti in Alexa</h3><p>Endpoint attivi: {len(current_endpoints)}</p><ul>{current_rows or '<li>Nessun endpoint attivo</li>'}</ul>"
    snapshot_heading = '<h3>Snapshot ultima Discovery completa (storico)</h3><p class="muted">Questo elenco fotografa esclusivamente l’ultima Discovery completa e non rappresenta necessariamente i dispositivi aggiunti più recentemente tramite sincronizzazione proattiva.</p>'
    if snapshot is None:
        return f'<section class="card"><h2>Alexa - ultima sincronizzazione</h2>{latest_activity}{snapshot_heading}<p>Nessuna sincronizzazione Alexa registrata</p>{reports}{current_inventory}</section>'
    changes = snapshot.changes_json or []
    change_by_endpoint = {
        str(change.get("endpoint_id")): str(change.get("change"))
        for change in changes
        if change.get("change") in {"new", "renamed"}
    }
    labels = {
        "new": "Nuovo rispetto alla Discovery precedente",
        "renamed": "Rinominato rispetto alla Discovery precedente",
        "removed": "Rimosso rispetto alla Discovery precedente",
    }

    def endpoint_line(endpoint: dict[str, object], change: str | None = None) -> str:
        badge = f'<span class="badge">{labels[change]}</span>' if change in labels else ""
        return f'<li><b>{_e(endpoint.get("voice_name"))}</b>{badge}<br><span class="muted">{_e(endpoint.get("endpoint_id"))} · {_e(endpoint.get("domain"))}</span></li>'

    current = "".join(
        endpoint_line(endpoint, change_by_endpoint.get(str(endpoint.get("endpoint_id"))))
        for endpoint in (snapshot.endpoints_json or [])
    )
    removed = "".join(
        endpoint_line(change, "removed") for change in changes if change.get("change") == "removed"
    )
    new_count = sum(change.get("change") == "new" for change in changes)
    discovered_at = snapshot.discovered_at.strftime("%d/%m/%Y %H:%M")
    items = current + removed
    return f'<section class="card"><h2>Alexa - ultima sincronizzazione</h2>{latest_activity}{snapshot_heading}<p>Ultima Discovery: {_e(discovered_at)}<br>Dispositivi inviati: {_e(snapshot.endpoint_count)}<br>Nuovi rispetto alla Discovery completa precedente: {new_count}</p>{reports}<ul>{items or "<li>Nessun dispositivo inviato</li>"}</ul>{current_inventory}</section>'


def _entity_row(installation: Installation, entity: Entity, csrf: str) -> str:
    operations = sorted(DOMAIN_OPERATIONS.get(entity.ha_domain, ()))
    enabled = bool(
        entity.deleted_at is None and entity.available and entity.ha_registry_id and operations
    )
    controls = _entity_controls(installation, entity, csrf, enabled)
    voice_name = effective_voice_name(entity)
    display_name = effective_display_name(entity)
    aliases = " · ".join(_e(alias) for alias in (entity.voice_aliases or [])) or "—"
    lifecycle = "rimossa" if entity.deleted_at else (entity.state or "—")
    edit = f'<a class="button" href="/installations/{installation.id}/entities/{entity.id}/edit">Modifica</a>'
    availability = (
        "disponibile" if entity.available and entity.deleted_at is None else "non disponibile"
    )
    if entity.deleted_at is not None:
        state_class = "removed"
    elif not entity.available:
        state_class = "unavailable"
    elif entity.state in {"on", "off"}:
        state_class = entity.state
    else:
        state_class = "neutral"
    icon = entity_icon_svg(entity.icon, entity.ha_domain)
    return f'<tr class="state-{state_class}" data-entity-row="{entity.id}"><td><div class="entity-summary">{icon}<div class="entity-meta"><span class="voice-label">Nome vocale: {_e(voice_name)}</span><br><b>{_e(voice_name)}</b><br><span class="muted">Nome visualizzato: {_e(display_name)}</span><br><span class="muted">Nome e-Control: {_e(entity.friendly_name or entity.ha_entity_id)}</span><br><span class="muted">entity_id: {_e(entity.ha_entity_id)}</span><br><span class="muted">Alias: {aliases}</span></div></div></td><td>{_e(entity.ha_domain)} / {_e(entity.area_name or "—")}</td><td><span class="status-dot state-{state_class}"></span><span class="entity-state">{_e(lifecycle)}</span><br><span class="muted">{availability}</span></td><td><div class="direct-controls">{controls}{edit}<span class="command-feedback" role="status" aria-live="polite"></span></div></td></tr>'


def _light_level(entity: Entity) -> int:
    brightness = (entity.attributes_json or {}).get("brightness")
    if not isinstance(brightness, int) or isinstance(brightness, bool):
        return 0
    return min(100, max(0, round(brightness * 100 / 255)))


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _climate_current_temperature(entity: Entity) -> float | None:
    """Return only the synchronized ambient temperature when it is usable."""
    return _finite_number((entity.attributes_json or {}).get("current_temperature"))


def _format_temperature(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _climate_target_config(
    entity: Entity,
) -> tuple[float, float | None, float | None, float | None] | None:
    attributes = entity.attributes_json or {}
    target = _finite_number(attributes.get("target_temp"))
    if target is None:
        target = _finite_number(attributes.get("temperature"))
    if target is None:
        return None
    minimum = _finite_number(attributes.get("min_temp"))
    maximum = _finite_number(attributes.get("max_temp"))
    if minimum is not None and maximum is not None and minimum > maximum:
        minimum = maximum = None
    step = _finite_number(attributes.get("target_temp_step"))
    return target, minimum, maximum, step if step is not None and step > 0 else None


def _climate_hvac_modes(entity: Entity) -> list[str]:
    values = (entity.attributes_json or {}).get("hvac_modes")
    if not isinstance(values, list):
        return []
    modes: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in modes:
            modes.append(value)
    return modes


def _control_form(
    installation: Installation,
    entity: Entity,
    csrf: str,
    operation: str,
    label: str,
    *,
    css_class: str = "",
    level: bool = False,
    level_value: int = 0,
    power: str | None = None,
    active: bool = False,
    enabled: bool,
) -> str:
    disabled = "" if enabled else " disabled"
    level_input = (
        f'<input type="range" name="value" min="0" max="100" value="{level_value}" step="1" aria-label="Livello luce percentuale"{disabled}><output class="level-value">{level_value}%</output>'
        if level
        else ""
    )
    form_class = "inline level-control" if level else "inline"
    power_data = f' data-power="{power}" aria-pressed="{str(active).lower()}"' if power else ""
    active_class = f" active-{power}" if active and power else ""
    return f'<form class="entity-command {form_class}" method="post" action="/installations/{installation.id}/commands"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="entity_id" value="{entity.id}"><input type="hidden" name="operation" value="{operation}">{level_input}<button class="command-button {css_class}{active_class}"{power_data}{disabled}>{label}</button></form>'


def _climate_controls(installation: Installation, entity: Entity, csrf: str, enabled: bool) -> str:
    disabled = "" if enabled else " disabled"
    controls: list[str] = []
    current_temperature = _climate_current_temperature(entity)
    if current_temperature is not None:
        controls.append(
            '<span class="climate-current-temperature">'
            f"Temperatura attuale: {_format_temperature(current_temperature)} &deg;C</span>"
        )
    target = _climate_target_config(entity)
    if target is not None:
        value, minimum, maximum, step = target
        bounds = "".join(
            (
                f' min="{_e(minimum)}"' if minimum is not None else "",
                f' max="{_e(maximum)}"' if maximum is not None else "",
                f' step="{_e(step)}"' if step is not None else ' step="any"',
            )
        )
        controls.append(
            f'<form class="entity-command inline climate-temperature-control" method="post" action="/installations/{installation.id}/commands"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="entity_id" value="{entity.id}"><input type="hidden" name="operation" value="set_target_temperature"><label>Temperatura target <input type="number" name="value" value="{_e(value)}"{bounds}{disabled}></label><button class="command-button"{disabled}>IMPOSTA TEMPERATURA</button></form>'
        )
    modes = _climate_hvac_modes(entity)
    if modes:
        options = "".join(
            f'<option value="{_e(mode)}"{" selected" if mode == entity.state else ""}>{_e(mode)}</option>'
            for mode in modes
        )
        controls.append(
            f'<form class="entity-command inline climate-mode-control" method="post" action="/installations/{installation.id}/commands"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="entity_id" value="{entity.id}"><input type="hidden" name="operation" value="set_hvac_mode"><label>Modalit&agrave; HVAC <select name="value"{disabled}>{options}</select></label><button class="command-button"{disabled}>IMPOSTA MODALIT&Agrave;</button></form>'
        )
    return "".join(controls) or '<span class="muted">Nessun controllo diretto</span>'


def _entity_controls(installation: Installation, entity: Entity, csrf: str, enabled: bool) -> str:
    if entity.ha_domain == "light":
        return "".join(
            (
                _control_form(
                    installation,
                    entity,
                    csrf,
                    "power_on",
                    "ON",
                    power="on",
                    active=entity.state == "on" and enabled,
                    enabled=enabled,
                ),
                _control_form(
                    installation,
                    entity,
                    csrf,
                    "power_off",
                    "OFF",
                    power="off",
                    active=entity.state == "off" and enabled,
                    enabled=enabled,
                ),
                _control_form(
                    installation,
                    entity,
                    csrf,
                    "set_brightness",
                    "SET LIGHT LEVEL",
                    level=True,
                    level_value=_light_level(entity),
                    enabled=enabled,
                ),
            )
        )
    if entity.ha_domain == "climate":
        return _climate_controls(installation, entity, csrf, enabled)
    simple_labels = {
        "power_on": "ON",
        "power_off": "OFF",
        "open": "APRI",
        "close": "CHIUDI",
        "stop": "STOP",
        "activate": "ATTIVA",
        "press": "PREMI",
    }
    return (
        "".join(
            _control_form(
                installation,
                entity,
                csrf,
                operation,
                simple_labels[operation],
                enabled=enabled,
            )
            for operation in sorted(DOMAIN_OPERATIONS.get(entity.ha_domain, ()))
            if operation in simple_labels
        )
        or '<span class="muted">Nessun controllo diretto</span>'
    )


def _entity_names_form(
    installation: Installation,
    entity: Entity,
    csrf: str,
    *,
    message: str = "",
    error: bool = False,
) -> str:
    aliases = "\n".join(entity.voice_aliases or [])
    notice = f'<p class="{"bad" if error else "ok"}">{_e(message)}</p>' if message else ""
    selected_device_type = entity.alexa_device_type or "auto"
    device_type_labels = {
        "auto": "Automatico (in base al tipo Home Assistant)",
        "switch": "Interruttore — accendi / spegni",
        "light": "Luce — accendi / spegni",
        "outlet": "Presa — accendi / spegni",
        "gate": "Cancello — apri / chiudi",
    }
    allowed_types = ("auto", *allowed_alexa_device_types(entity))
    device_type_options = "".join(
        f'<option value="{value}"{" selected" if value == selected_device_type else ""}>{device_type_labels[value]}</option>'
        for value in allowed_types
    )
    device_type = f"""<label class="field"><b>Tipo dispositivo Alexa</b><select name="alexa_device_type">{device_type_options}</select><span class="muted">Non cambia il tipo reale in Home Assistant. Determina categoria, capability e verbi vocali pubblicati ad Alexa.</span></label>"""
    cover_mode = ""
    if entity.ha_domain == "cover":
        selected = entity.alexa_cover_mode or "auto"
        supports_stop = bool(entity.supported_features & COVER_STOP)
        labels = {
            "auto": "Automatico (in base alle funzioni e-Control)",
            "discrete": (
                "Discreto — apri / stop / chiudi" if supports_stop else "Discreto — apri e chiudi"
            ),
            "percentage": "Percentuale — posizione 0–100%",
            "hybrid": "Ibrido — comandi discreti e percentuali",
        }
        options = "".join(
            f'<option value="{value}"{" selected" if value == selected else ""}>{label}</option>'
            for value, label in labels.items()
        )
        effective = effective_cover_mode(entity) or "non pubblicabile con le funzioni attuali"
        discrete_help = (
            "Discreto usa i comandi stateless apri, ferma e chiudi"
            if supports_stop
            else "Discreto usa i comandi stateless apri e chiudi"
        )
        cover_mode = f"""<label class="field"><b>Modalità Alexa tapparella/tenda</b><select name="alexa_cover_mode">{options}</select><span class="muted">{discrete_help}, senza percentuali; Percentuale usa la posizione 0–100%; Ibrido espone entrambi. Modalità effettiva: {_e(effective)}.</span></label>"""
    return f'''{notice}<div class="card"><p><b>Nome e-Control</b><br>{_e(entity.friendly_name or entity.ha_entity_id)}<br><span class="muted">Sincronizzato automaticamente e non modificabile qui.</span></p>
<form method="post"><input type="hidden" name="csrf_token" value="{_e(csrf)}">
<label class="field"><b>Nome visualizzato</b><input name="display_name" maxlength="120" value="{_e(entity.display_name)}" placeholder="Fallback: {_e(entity.friendly_name or entity.ha_entity_id)}"><span class="muted">Se vuoto: Nome e-Control.</span></label>
<label class="field"><b>Nome vocale</b><input name="voice_name" maxlength="120" value="{_e(entity.voice_name)}" placeholder="Fallback: {_e(effective_display_name(entity))}"><span class="muted">Se vuoto: Nome visualizzato → Nome e-Control.</span></label>
<label class="field"><b>Alias vocali</b><textarea name="voice_aliases" maxlength="2420" placeholder="Un alias per riga">{_e(aliases)}</textarea><span class="muted">Massimo 20 alias; spazi e duplicati senza distinzione maiuscole/minuscole vengono normalizzati.</span></label>
{device_type}
{cover_mode}
<p><b>Nome dashboard effettivo:</b> {_e(effective_display_name(entity))}<br><b>Nome vocale effettivo:</b> {_e(effective_voice_name(entity))}<br><b>Tutti i nomi vocali:</b> {_e(", ".join(all_voice_names(entity)))}</p>
<div class="actions"><button name="action" value="save">Salva</button><a class="button" href="/installations/{installation.id}">Annulla</a><button class="danger" name="action" value="reset">Ripristina nomi personalizzati</button></div></form></div>'''


def _names_page(
    installation: Installation,
    entity: Entity,
    context: TenantContext,
    csrf: str,
    *,
    message: str = "",
    error: bool = False,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    response = HTMLResponse(
        _layout(
            "Modifica nomi entità",
            _entity_names_form(installation, entity, csrf, message=message, error=error),
            context,
            csrf,
            "installations",
        ),
        status_code=status_code,
    )
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


@router.get(
    "/installations/{installation_id}/entities/{entity_id}/edit", response_class=HTMLResponse
)
async def edit_entity_names_page(
    installation_id: UUID,
    entity_id: UUID,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _admin(context)
    installation = await _installation(session, context, installation_id)
    entity = await _entity(session, installation, entity_id)
    csrf = _csrf(context)
    return _names_page(installation, entity, context, csrf)


@router.post(
    "/installations/{installation_id}/entities/{entity_id}/edit", response_class=HTMLResponse
)
async def update_entity_names(
    installation_id: UUID,
    entity_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _admin(context)
    installation = await _installation(session, context, installation_id)
    entity = await _entity(session, installation, entity_id)
    values = await _form(request)
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    previous = (
        entity.display_name,
        entity.voice_name,
        list(entity.voice_aliases or []),
        entity.alexa_cover_mode,
        entity.alexa_device_type,
    )
    try:
        if values.get("action") == "reset":
            entity.display_name, entity.voice_name, entity.voice_aliases = None, None, []
        else:
            entity.display_name = clean_optional_name(values.get("display_name", ""))
            entity.voice_name = clean_optional_name(values.get("voice_name", ""))
            entity.voice_aliases = clean_voice_aliases(
                re.split(r"[\r\n,]+", values.get("voice_aliases", ""))
            )
            requested_device_type = values.get("alexa_device_type", "auto")
            entity.alexa_device_type = (
                None
                if requested_device_type == "auto"
                else validate_alexa_device_type(entity, requested_device_type)
            )
            if entity.ha_domain == "cover":
                requested_mode = values.get("alexa_cover_mode", "auto")
                entity.alexa_cover_mode = (
                    None
                    if requested_mode == "auto"
                    else validate_cover_mode(entity, requested_mode)
                )
    except ValueError:
        (
            entity.display_name,
            entity.voice_name,
            entity.voice_aliases,
            entity.alexa_cover_mode,
            entity.alexa_device_type,
        ) = previous
        return _names_page(
            installation,
            entity,
            context,
            _csrf(context),
            message=(
                "Modalità Alexa incompatibile con le funzioni e-Control disponibili."
                if entity.ha_domain == "cover"
                else "Valori troppo lunghi o troppi alias."
            ),
            error=True,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    entities = list(
        (
            await session.scalars(
                select(Entity).where(
                    Entity.installation_id == installation.id, Entity.deleted_at.is_(None)
                )
            )
        ).all()
    )
    if any(entity.id in ids for ids in voice_collisions(entities).values()):
        (
            entity.display_name,
            entity.voice_name,
            entity.voice_aliases,
            entity.alexa_cover_mode,
            entity.alexa_device_type,
        ) = previous
        return _names_page(
            installation,
            entity,
            context,
            _csrf(context),
            message="Nome vocale o alias già utilizzato da un’altra entità.",
            error=True,
            status_code=status.HTTP_409_CONFLICT,
        )
    current = (
        entity.display_name,
        entity.voice_name,
        list(entity.voice_aliases or []),
        entity.alexa_cover_mode,
        entity.alexa_device_type,
    )
    changed_fields = [
        name
        for name, before, after in zip(
            ("display_name", "voice_name", "voice_aliases", "alexa_cover_mode", "alexa_device_type"),
            previous,
            current,
            strict=True,
        )
        if before != after
    ]
    session.add(
        AuditEvent(
            tenant_id=context.tenant_id,
            installation_id=installation.id,
            user_id=context.user_id,
            source="admin_console",
            event_type=(
                "entity_names.reset" if values.get("action") == "reset" else "entity_names.updated"
            ),
            payload_redacted_json={"entity_id": str(entity.id), "changed_fields": changed_fields},
            result="success",
        )
    )
    await session.commit()
    await reconcile_discovery_safely(session, installation)
    return _names_page(installation, entity, context, _csrf(context), message="Configurazione entità salvata.")


def _command_data(operation: str, value: str) -> dict[str, object]:
    data: dict[str, object] = {"operation": operation}
    if operation == "set_brightness":
        percentage = int(value)
        if not 0 <= percentage <= 100:
            raise ValueError("brightness percentage outside bounds")
        data["brightness"] = round(percentage * 255 / 100)
    elif operation == "set_position":
        data["position"] = int(value)
    elif operation == "set_target_temperature":
        data["temperature"] = float(value)
    elif operation == "set_hvac_mode":
        data["hvac_mode"] = value
    elif operation == "set_percentage":
        data["percentage"] = int(value)
    return data


def _validate_climate_value(entity: Entity, operation: str, value: str) -> None:
    if operation == "set_target_temperature":
        config = _climate_target_config(entity)
        if config is None:
            raise ValueError("target temperature capability unavailable")
        requested = float(value)
        _, minimum, maximum, _ = config
        if minimum is not None and requested < minimum:
            raise ValueError("target temperature below entity minimum")
        if maximum is not None and requested > maximum:
            raise ValueError("target temperature above entity maximum")
    elif operation == "set_hvac_mode" and value not in _climate_hvac_modes(entity):
        raise ValueError("HVAC mode not advertised by entity")


@router.post("/installations/{installation_id}/commands", response_model=None)
async def send_command(
    installation_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse | JSONResponse:
    _admin(context)
    installation = await _installation(session, context, installation_id)
    values = await _form(request)
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse(
                {"detail": "Richiesta non valida", "code": "csrf_invalid"},
                status_code=status.HTTP_403_FORBIDDEN,
            )
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
        if entity.ha_domain == "climate":
            _validate_climate_value(
                entity,
                values.get("operation", ""),
                values.get("value", ""),
            )
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
    if "application/json" in request.headers.get("accept", ""):
        succeeded = outcome.status == "success"
        target_state = None
        if succeeded and command.operation in {"power_on", "power_off"}:
            target_state = "on" if command.operation == "power_on" else "off"
        response_payload: dict[str, object] = {
            "ok": succeeded,
            "message": "Comando eseguito" if succeeded else "Comando non riuscito",
            "status": outcome.status,
            "state": target_state,
        }
        if succeeded and command.operation in {"set_target_temperature", "set_hvac_mode"}:
            command_payload = command.model_dump(mode="json")
            response_payload["value"] = command_payload.get(
                "temperature" if command.operation == "set_target_temperature" else "hvac_mode"
            )
        return JSONResponse(
            response_payload,
            status_code=status.HTTP_200_OK if succeeded else status.HTTP_502_BAD_GATEWAY,
        )
    csrf = _csrf(context)
    body = f'<div class="card"><b>Esito: {_e(outcome.status)}</b><p>Il comando è stato completato dal dispatcher EVCP; nessun esito è simulato.</p><a class="button" href="/installations/{installation.id}">Torna all’installazione</a></div>'
    response = HTMLResponse(_layout("Esito comando", body, context, csrf, "installations"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


ACTIVITY_FILTER_PARAMS = (
    "installation_id",
    "source",
    "outcome",
    "event_type",
    "entity_id",
    "correlation_id",
    "command_id",
    "endpoint_id",
    "ha_entity_id",
    "from",
    "to",
)


def _activity_export_inputs(request: Request) -> str:
    return "".join(
        f'<input type="hidden" name="{_e(key)}" value="{_e(request.query_params[key])}">'
        for key in ACTIVITY_FILTER_PARAMS
        if request.query_params.get(key)
    )


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
    source_filter = request.query_params.get("source", "")
    if source_filter:
        aq = aq.where(AuditEvent.source == source_filter)
        oq = oq.where(OperationalEvent.source == source_filter)
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
    diagnostic_filters = {
        key: request.query_params.get(key, "")
        for key in ("correlation_id", "command_id", "endpoint_id", "ha_entity_id")
    }
    for key, value in diagnostic_filters.items():
        if value:
            aq = aq.where(AuditEvent.payload_redacted_json[key].as_string() == value)
            oq = oq.where(OperationalEvent.metadata_json[key].as_string() == value)
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
    all_events: list[ActivityRow] = sorted(
        [
            *(
                ActivityRow(
                    at=e.created_at,
                    kind=e.event_type,
                    source=e.source,
                    result=e.result,
                    installation_id=e.installation_id,
                    request_id=e.request_id,
                    detail=e.payload_redacted_json,
                )
                for e in audits
            ),
            *(
                ActivityRow(
                    at=e.created_at,
                    kind=e.event_type,
                    source=e.source,
                    result=e.outcome,
                    installation_id=e.installation_id,
                    request_id=None,
                    detail=e.metadata_json,
                )
                for e in operations
            ),
        ],
        key=lambda item: item["at"],
        reverse=not bool(diagnostic_filters["correlation_id"]),
    )
    page = max(1, int(request.query_params.get("page", "1")))
    events = all_events[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    rows = "".join(
        f'<tr data-correlation-id="{_e(event["detail"].get("correlation_id", ""))}">'
        f"<td>{_e(event['at'])}</td><td><details><summary>{_e(event['kind'])}</summary>"
        f"<pre>{_e(json.dumps(event['detail'], indent=2, ensure_ascii=False, default=str))}</pre>"
        f"</details></td><td>{_e(event['source'])}</td><td>{_e(event['result'])}</td>"
        f"<td>{_e(event['detail'].get('correlation_id'))}</td>"
        f"<td>{_e(event['detail'].get('command_id'))}</td>"
        f"<td>{_e(event['detail'].get('endpoint_id'))}</td>"
        f"<td>{_e(event['detail'].get('ha_entity_id'))}</td>"
        f"<td>{_e(event['detail'].get('operation'))}</td>"
        f"<td>{_e(event['installation_id'])}</td></tr>"
        for event in events
    )
    csrf = _csrf(context)
    body = f'''<form method="get"><input name="installation_id" placeholder="ID installazione" value="{_e(installation_filter)}"><input name="source" placeholder="Fonte (es. alexa)" value="{_e(source_filter)}"><input name="outcome" placeholder="Esito" value="{_e(outcome)}"><input name="correlation_id" placeholder="Correlation ID" value="{_e(diagnostic_filters["correlation_id"])}"><input name="command_id" placeholder="Command ID" value="{_e(diagnostic_filters["command_id"])}"><input name="endpoint_id" placeholder="Endpoint ID" value="{_e(diagnostic_filters["endpoint_id"])}"><input name="ha_entity_id" placeholder="HA entity ID" value="{_e(diagnostic_filters["ha_entity_id"])}"><button>Filtra</button></form><table><thead><tr><th>Data</th><th>Evento / JSON</th><th>Fonte</th><th>Esito</th><th>Correlation</th><th>Command</th><th>Endpoint</th><th>HA entity</th><th>Operation</th><th>Installazione</th></tr></thead><tbody>{rows or "<tr><td colspan=10>Nessuna attività</td></tr>"}</tbody></table>'''
    body = f"""<form method="get" action="/activity/export" class="actions">{_activity_export_inputs(request)}<button name="format" value="json">Esporta attività JSON</button><button name="format" value="csv">Esporta attività CSV</button></form>{body}"""
    response = HTMLResponse(_layout("Attività", body, context, csrf, "activity"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


def _export_activity_row(
    event: AuditEvent | OperationalEvent,
    installation_metadata: dict[str, dict[str, object | None]],
) -> dict[str, object | None]:
    if isinstance(event, AuditEvent):
        payload = event.payload_redacted_json
        outcome = event.result
        request_id: object | None = event.request_id
    else:
        payload = event.metadata_json
        outcome = event.outcome
        request_id = payload.get("request_id")
    installation_id = str(event.installation_id) if event.installation_id else None
    installation = installation_metadata.get(installation_id or "", {})
    return {
        "timestamp": event.created_at.isoformat(),
        "event_type": event.event_type,
        "source": event.source,
        "outcome": outcome,
        "request_id": request_id,
        "correlation_id": payload.get("correlation_id"),
        "command_id": payload.get("command_id"),
        "endpoint_id": payload.get("endpoint_id"),
        "ha_entity_id": payload.get("ha_entity_id"),
        "operation": payload.get("operation"),
        "installation_id": installation_id,
        "ha_version": installation.get("ha_version"),
        "connector_version": installation.get("connector_version"),
        "connector_protocol_version": installation.get("connector_protocol_version"),
        "connector_compatibility_status": installation.get("compatibility_status"),
        "connector_compatibility_reason": installation.get("compatibility_reason"),
        "payload": payload,
    }


@router.get("/activity/export")
async def export_activity(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    format: str = "json",
) -> Response:
    """Export every tenant-scoped Activity event matching the page filters."""
    _admin(context)
    if format not in {"json", "csv"}:
        raise HTTPException(422, "Formato export non valido")

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
        aq = aq.where(AuditEvent.installation_id == installation_id)
        oq = oq.where(OperationalEvent.installation_id == installation_id)

    outcome = request.query_params.get("outcome", "")
    if outcome:
        aq = aq.where(AuditEvent.result == outcome)
        oq = oq.where(OperationalEvent.outcome == outcome)
    source_filter = request.query_params.get("source", "")
    if source_filter:
        aq = aq.where(AuditEvent.source == source_filter)
        oq = oq.where(OperationalEvent.source == source_filter)
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

    for key in ("correlation_id", "command_id", "endpoint_id", "ha_entity_id"):
        if value := request.query_params.get(key, ""):
            aq = aq.where(AuditEvent.payload_redacted_json[key].as_string() == value)
            oq = oq.where(OperationalEvent.metadata_json[key].as_string() == value)

    date_from, date_to = (request.query_params.get(key, "") for key in ("from", "to"))
    try:
        if date_from:
            start = datetime.fromisoformat(date_from).replace(tzinfo=UTC)
            aq = aq.where(AuditEvent.created_at >= start)
            oq = oq.where(OperationalEvent.created_at >= start)
        if date_to:
            end = datetime.fromisoformat(date_to).replace(tzinfo=UTC) + timedelta(days=1)
            aq = aq.where(AuditEvent.created_at < end)
            oq = oq.where(OperationalEvent.created_at < end)
    except ValueError as error:
        raise HTTPException(422, "Data non valida") from error

    audits = list((await session.scalars(aq.order_by(AuditEvent.created_at.desc()))).all())
    operations = list(
        (await session.scalars(oq.order_by(OperationalEvent.created_at.desc()))).all()
    )
    all_activity_events: list[AuditEvent | OperationalEvent] = [*audits, *operations]
    installation_ids = {
        event.installation_id for event in all_activity_events if event.installation_id
    }
    if installation_id:
        installation_ids.add(installation_id)
    installations = (
        list(
            (
                await session.scalars(
                    select(Installation).where(
                        Installation.tenant_id == context.tenant_id,
                        Installation.id.in_(installation_ids),
                    )
                )
            ).all()
        )
        if installation_ids
        else []
    )
    installation_metadata: dict[str, dict[str, object | None]] = {
        str(item.id): {
            "installation_id": str(item.id),
            "name": item.name,
            "ha_version": item.ha_version,
            "connector_version": item.connector_version,
            "connector_protocol_version": item.connector_protocol_version,
            "compatibility_status": _compatibility_status(item).value,
            "compatibility_reason": item.connector_compatibility_reason,
            "last_seen": item.last_seen_at.isoformat() if item.last_seen_at else None,
        }
        for item in installations
    }
    rows = sorted(
        (_export_activity_row(event, installation_metadata) for event in all_activity_events),
        key=lambda row: str(row["timestamp"]),
        reverse=True,
    )
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="ekonex-voice-activity-{stamp}.{format}"',
    }
    if format == "json":
        return JSONResponse(
            {
                "exported_at": now.isoformat(),
                "tenant_id": str(context.tenant_id),
                "filters": {
                    key: request.query_params[key]
                    for key in ACTIVITY_FILTER_PARAMS
                    if request.query_params.get(key)
                },
                "connector_requirements": {
                    "minimum_supported": MINIMUM_SUPPORTED_CONNECTOR_VERSION,
                    "recommended": RECOMMENDED_CONNECTOR_VERSION,
                    "evcp_protocol": REQUIRED_EVCP_PROTOCOL_VERSION,
                },
                "installations": list(installation_metadata.values()),
                "activities": rows,
            },
            headers=headers,
        )

    fieldnames = [
        "timestamp",
        "event_type",
        "source",
        "outcome",
        "request_id",
        "correlation_id",
        "command_id",
        "endpoint_id",
        "ha_entity_id",
        "operation",
        "installation_id",
        "ha_version",
        "connector_version",
        "connector_protocol_version",
        "connector_compatibility_status",
        "connector_compatibility_reason",
        "payload_json",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        csv_row = {key: row.get(key) for key in fieldnames if key != "payload_json"}
        csv_row["payload_json"] = json.dumps(
            row["payload"], ensure_ascii=False, separators=(",", ":"), default=str
        )
        writer.writerow(csv_row)
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/system", response_class=HTMLResponse)
async def system_stats(
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _admin(context)
    installation_items = list(
        (
            await session.scalars(
                select(Installation)
                .where(Installation.tenant_id == context.tenant_id)
                .order_by(Installation.name)
            )
        ).all()
    )
    installations = len(installation_items)
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
    database_size_mb = await _database_size_mb(session)
    settings = get_settings()
    maintenance: MaintenanceRun | None = await latest_cleanup(session)
    now = datetime.now(UTC)
    next_run = next_cleanup_at(
        now,
        maintenance.started_at if maintenance else None,
        schedule_hour_utc=settings.cleanup_schedule_hour_utc,
    )
    last_run = (maintenance.completed_at or maintenance.started_at) if maintenance else "Mai"
    last_result = maintenance.status.upper() if maintenance else "NON ESEGUITA"
    size_display = f"{database_size_mb:.2f} MB" if database_size_mb is not None else "n/d"
    compatibility_rows = "".join(
        f'<tr><td><a href="/installations/{item.id}">{_e(item.name)}</a></td><td>{_e(item.connector_version or "—")}</td><td>{_e(item.ha_version or "—")}</td><td>{_e(item.connector_protocol_version or "—")}</td><td>{_e(item.last_seen_at or "—")}</td><td>{_e(MINIMUM_SUPPORTED_CONNECTOR_VERSION)}</td><td>{_e(RECOMMENDED_CONNECTOR_VERSION)}</td><td>{_compatibility_badge(item)}</td></tr>'
        for item in installation_items
    )
    compatibility_table = f"<h2>Compatibilità Cloud ↔ Connector</h2><table><thead><tr><th>Installation</th><th>Connector version</th><th>HA version</th><th>EVCP protocol</th><th>Last seen</th><th>Minimum supported</th><th>Recommended</th><th>Compatibility status</th></tr></thead><tbody>{compatibility_rows or '<tr><td colspan=8>Nessuna installazione</td></tr>'}</tbody></table>"
    csrf = _csrf(context)
    body = f'<div class="cards"><div class="card"><b>{installations}</b><br>Installazioni</div><div class="card"><b>{entities}</b><br>Entità</div><div class="card"><b>{history}</b><br>Campioni storico</div><div class="card"><b>{audit}</b><br>Eventi audit</div><div class="card"><b>{_e(size_display)}</b><br>Dimensione reale DB</div></div>{compatibility_table}<h2>Manutenzione automatica</h2><div class="cards"><div class="card"><b>{_e(last_run)}</b><br>Ultima pulizia</div><div class="card"><b>{_e(last_result)}</b><br>Esito ultima pulizia</div><div class="card"><b>{_e(next_run)}</b><br>Prossima pulizia prevista</div></div><h2>Retention configurata</h2><ul><li>Storico stati: {settings.state_history_retention_days} giorni</li><li>Eventi operativi: {settings.operational_event_retention_days} giorni</li><li>Audit amministrativo: {settings.admin_audit_retention_days} giorni</li><li>Tentativi login: {settings.portal_login_attempt_retention_days} giorni</li><li>Sessioni portale: eliminate dopo la scadenza</li></ul>'
    response = HTMLResponse(_layout("Sistema", body, context, csrf, "system"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response
