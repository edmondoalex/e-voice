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
from sqlalchemy.orm import selectinload

from .alexa_device_types import allowed_alexa_device_types, validate_alexa_device_type
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
    VoiceCategory,
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
from .laboratory_live_state import (
    load_live_installation,
    load_live_states,
    overlay_entities,
    overlay_installation,
    sync_live_entities,
)
from .maintenance import latest_cleanup, next_cleanup_at
from .pairing_api import CSRF_COOKIE, _csrf, _form, _valid_csrf, identity_dependency
from .portal_auth import PortalIdentity
from .voice_categories import STANDARD_VOICE_CATEGORIES, category_slug, infer_standard_category


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
    "fan": {
        "power_on",
        "power_off",
        "set_percentage",
        "set_preset_mode",
        "oscillate_on",
        "oscillate_off",
        "direction_forward",
        "direction_reverse",
    },
    "scene": {"activate"},
    "script": {"activate"},
    "button": {"press"},
    "select": {"select_option"},
    "lock": {"lock", "unlock"},
    "alarm_control_panel": {"arm_home", "arm_away", "disarm"},
    "vacuum": {"start", "stop", "return_to_base"},
    "valve": {"open", "close"},
    "water_heater": {"power_on", "power_off", "set_target_temperature"},
    "humidifier": {"power_on", "power_off", "set_percentage", "set_mode"},
    "camera": {"camera_snapshot"},
    "media_player": {
        "power_on",
        "power_off",
        "set_volume",
        "volume_mute",
        "volume_unmute",
        "media_play",
        "media_pause",
        "media_stop",
        "media_next",
        "media_previous",
        "select_source",
        "media_artwork",
        "media_join",
        "media_unjoin",
        "set_group_volume",
    },
}

MEDIA_PLAYER_FEATURE_PAUSE = 1
MEDIA_PLAYER_FEATURE_VOLUME_SET = 4
MEDIA_PLAYER_FEATURE_VOLUME_MUTE = 8
MEDIA_PLAYER_FEATURE_PREVIOUS_TRACK = 16
MEDIA_PLAYER_FEATURE_NEXT_TRACK = 32
MEDIA_PLAYER_FEATURE_TURN_ON = 128
MEDIA_PLAYER_FEATURE_TURN_OFF = 256
MEDIA_PLAYER_FEATURE_STOP = 4096
MEDIA_PLAYER_FEATURE_PLAY = 16384
MEDIA_PLAYER_FEATURE_SELECT_SOURCE = 2048
MEDIA_PLAYER_FEATURE_GROUPING = 524288
FAN_FEATURE_SET_SPEED = 1
FAN_FEATURE_OSCILLATE = 2
FAN_FEATURE_DIRECTION = 4
FAN_FEATURE_PRESET_MODE = 8


def _media_sources(entity: Entity) -> list[str]:
    values = (entity.attributes_json or {}).get("source_list")
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))[:64]


def _media_source_setting(entity: Entity, source: str) -> dict[str, object]:
    settings = entity.media_source_settings or {}
    value = settings.get(source)
    return value if isinstance(value, dict) else {}


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
    is_laboratory = get_settings().environment == "laboratory"
    laboratory_banner = (
        '<div class="laboratory-banner">LABORATORIO — NON È PRODUZIONE</div>'
        if is_laboratory
        else ""
    )
    logo_filename = "ekonex-e-voice-laboratorio.png" if is_laboratory else "ekonex-cloud-voice.png"
    logo_alt = "Ekonex Laboratorio e-Voice" if is_laboratory else "Ekonex Cloud Voice"
    navigation = "".join(
        (
            _nav_link("/dashboard", "Dashboard", "dashboard", active),
            _nav_link("/installations", "Impianti", "installations", active),
            _nav_link("/voice-categories", "Categorie sensori", "voice-categories", active),
            _nav_link("/alexa-routines", "Routine vocali", "alexa-routines", active),
            _nav_link("/doorbells", "Campanelli", "doorbells", active),
            _nav_link("/control4-favorites", "Preferiti musicali", "control4-favorites", active),
            _nav_link("/laboratory/learning", "Apprendimento IA", "ai-learning", active),
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
.entity-group{{margin-top:12px;background:var(--card);border-radius:10px;box-shadow:0 1px 3px #10182818;overflow:hidden}}.entity-group summary{{display:flex;align-items:center;gap:12px;padding:16px 18px;cursor:pointer;font-weight:800;list-style:none}}.entity-group summary::-webkit-details-marker{{display:none}}.entity-group summary::before{{content:"▶";font-size:12px;color:var(--blue);transition:transform .15s ease}}.entity-group[open] summary::before{{transform:rotate(90deg)}}.entity-group-count{{margin-left:auto;padding:3px 9px;border-radius:999px;background:#e8f0fe;color:#174ea6;font-size:12px}}.entity-group table{{margin:0;border-radius:0;box-shadow:none;border-top:1px solid #eaecf0}}
th,td{{padding:12px;text-align:left;border-bottom:1px solid #eaecf0}}input,select,textarea,button{{padding:9px;border:1px solid #d0d5dd;border-radius:7px;font:inherit}}textarea{{width:100%;min-height:120px}}
button,.button{{background:var(--blue);color:white;border:0;text-decoration:none;display:inline-block;padding:9px 12px;border-radius:7px}}
.ok{{color:var(--ok)}}.bad{{color:var(--bad)}}.warn{{color:var(--removed)}}.muted{{color:var(--muted)}}.badge{{display:inline-block;margin-left:6px;padding:2px 7px;border-radius:999px;background:#e8f0fe;color:#174ea6;font-size:12px;font-weight:700}}.compat-badge{{display:inline-block;padding:4px 9px;border-radius:999px;font-size:12px;font-weight:800}}.compat-ok{{background:#dcfae6;color:var(--ok)}}.compat-update{{background:#fef0c7;color:#93370d}}.compat-bad{{background:#fee4e2;color:var(--bad)}}.compat-offline{{background:#eaecf0;color:var(--off)}}.compat-alert{{border:2px solid var(--bad);background:#fff5f4}}.global-warning{{border-left:6px solid var(--bad);background:#fff5f4;margin-bottom:16px}}form.inline{{display:inline}}.field{{display:block;margin:16px 0}}.field input{{display:block;width:100%;margin-top:6px}}.actions,.direct-controls{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}button.danger{{background:var(--bad)}}.command-button{{background:#e4e7ec;color:var(--ink)}}.command-button.active-on{{background:var(--ok);color:white;font-weight:700}}.command-button.active-off{{background:var(--off);color:white;font-weight:700}}button:disabled,input:disabled{{opacity:.45;cursor:not-allowed}}.entity-summary{{display:flex;align-items:flex-start;gap:10px;min-width:250px}}.entity-icon{{flex:0 0 auto;fill:var(--blue)}}.entity-meta{{line-height:1.45}}.voice-label{{font-size:12px;color:var(--blue);font-weight:700;text-transform:uppercase}}.status-dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;background:var(--off)}}.status-dot.state-on{{background:var(--ok)}}.status-dot.state-off{{background:var(--off)}}.status-dot.state-unavailable{{background:var(--bad)}}.status-dot.state-removed{{background:var(--removed)}}.level-control input{{width:110px;padding:0}}.level-value{{min-width:38px;font-variant-numeric:tabular-nums}}.command-feedback{{flex-basis:100%;min-height:20px;font-size:13px}}tr.state-on td:first-child{{box-shadow:inset 3px 0 var(--ok)}}@media(max-width:720px){{aside{{position:static;width:auto}}main{{margin:0;padding:16px}}table{{display:block;overflow:auto}}}}
.laboratory-banner{{background:#b42318;color:white;padding:12px 16px;border-radius:8px;font-weight:900;text-align:center;margin-bottom:18px;letter-spacing:.04em}}
</style></head><body><aside><img class="brand-logo" src="/static/{logo_filename}" width="1254" height="1254" alt="{logo_alt}">
<nav aria-label="Navigazione principale">{navigation}</nav>
<form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><button>Esci</button></form>
</aside><main>{laboratory_banner}<p class="muted">Tenant: {_e(context.tenant_id)}</p><h1>{_e(title)}</h1>{body}</main><script>
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
      if (payload.image_data_url) {{
        const preview = row.querySelector('.command-image-preview');
        preview.src = payload.image_data_url;
        preview.hidden = false;
        feedback.textContent = 'Immagine aggiornata';
      }}
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


async def _voice_categories(session: AsyncSession, context: TenantContext) -> list[VoiceCategory]:
    items = list(
        (
            await session.scalars(
                select(VoiceCategory)
                .where(VoiceCategory.tenant_id == context.tenant_id)
                .order_by(VoiceCategory.name)
            )
        ).all()
    )
    existing_slugs = {item.slug for item in items}
    missing = [
        VoiceCategory(
            tenant_id=context.tenant_id,
            slug=slug,
            name=name,
            description=description,
            builtin=True,
        )
        for slug, name, description in STANDARD_VOICE_CATEGORIES
        if slug not in existing_slugs
    ]
    if not missing:
        return items
    session.add_all(missing)
    await session.commit()
    return list(
        (
            await session.scalars(
                select(VoiceCategory)
                .where(VoiceCategory.tenant_id == context.tenant_id)
                .order_by(VoiceCategory.name)
            )
        ).all()
    )


@router.get("/voice-categories", response_class=HTMLResponse)
async def voice_categories_page(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    _admin(context)
    csrf = _csrf(context)
    categories = await _voice_categories(session, context)
    uncategorized_result = await session.execute(
        select(Entity, Installation.name)
        .join(Installation)
        .where(
            Installation.tenant_id == context.tenant_id,
            Entity.deleted_at.is_(None),
            Entity.voice_category_id.is_(None),
        )
        .order_by(Installation.name, Entity.ha_domain, Entity.friendly_name, Entity.ha_entity_id)
    )
    uncategorized = list(uncategorized_result.all())
    categorized_entities = list(
        (
            await session.scalars(
                select(Entity)
                .join(Installation)
                .where(
                    Installation.tenant_id == context.tenant_id,
                    Entity.deleted_at.is_(None),
                    Entity.voice_category_id.is_not(None),
                )
                .order_by(Entity.friendly_name, Entity.ha_entity_id)
            )
        ).all()
    )
    examples = {
        "photovoltaic_power": "Quanto produce il fotovoltaico?",
        "consumption_power": "Quanto sta consumando la casa?",
        "battery_level": "Come sono messe tutte le batterie?",
        "temperature": "Quali sono le temperature ambiente?",
        "thermal_temperature": "Temperature della centrale termica",
        "alarm_status": "Qual è lo stato dell'allarme?",
        "opening_status": "Quali porte risultano aperte?",
        "lock_status": "Qual è lo stato delle serrature?",
    }
    category_cards: list[str] = []
    for item in categories:
        members = [entity for entity in categorized_entities if entity.voice_category_id == item.id]
        member_rows = "".join(
            f"<li><b>{_e(effective_display_name(entity))}</b> "
            f'<span class="muted">{_e(entity.ha_entity_id)}</span> '
            f'<a class="button" href="/installations/{entity.installation_id}/entities/{entity.id}/edit">Modifica</a></li>'
            for entity in members
        )
        detail = (
            f'<ul class="category-entities">{member_rows}</ul>'
            if members
            else '<p class="bad">Categoria vuota</p>'
        )
        category_cards.append(
            f'<details class="card category-card"><summary><b>{_e(item.name)}</b> — '
            f'{len(members)} entità</summary><p class="muted">Esempio: '
            f"{_e(examples.get(item.slug, f'Tutti i valori {item.name}'))}</p>{detail}</details>"
        )
    category_summary = "".join(category_cards)
    rows = "".join(
        f'<tr><td><b>{_e(item.name)}</b><br><span class="muted">{_e(item.slug)}</span></td>'
        f"<td>{_e(item.description or '—')}</td><td>{'Standard' if item.builtin else 'Personalizzata'}</td></tr>"
        for item in categories
    )
    assigned = request.query_params.get("assigned", "").strip()
    result = (
        f'<div class="card ok"><b>Classificazione completata:</b> {_e(assigned)} entità assegnate. Le categorie già presenti non sono state modificate.</div>'
        if assigned.isdigit()
        else ""
    )
    bulk_assigned = request.query_params.get("bulk_assigned", "").strip()
    if bulk_assigned.isdigit():
        result += f'<div class="card ok"><b>Assegnazione in massa completata:</b> {_e(bulk_assigned)} entità assegnate.</div>'
    category_options = "".join(
        f'<option value="{item.id}">{_e(item.name)}</option>' for item in categories
    )
    uq = request.query_params.get("uq", "").strip().casefold()
    ui = request.query_params.get("uinstallation", "").strip()
    ud = request.query_params.get("udomain", "").strip()
    ua = request.query_params.get("uarea", "").strip()
    visible_uncategorized = [
        (entity, installation_name)
        for entity, installation_name in uncategorized
        if (
            not uq
            or uq
            in " ".join(
                filter(None, (entity.ha_entity_id, entity.friendly_name, entity.device_class))
            ).casefold()
        )
        and (not ui or str(entity.installation_id) == ui)
        and (not ud or entity.ha_domain == ud)
        and (not ua or (entity.area_name or "") == ua)
    ]

    def _options(values: set[str], selected: str) -> str:
        return "".join(
            f'<option value="{_e(value)}"{" selected" if value == selected else ""}>{_e(value)}</option>'
            for value in sorted(values)
        )

    installation_options = "".join(
        f'<option value="{entity.installation_id}"{" selected" if str(entity.installation_id) == ui else ""}>{_e(name)}</option>'
        for entity, name in {
            entity.installation_id: (entity, name) for entity, name in uncategorized
        }.values()
    )
    unassigned_rows = (
        "".join(
            f'<label class="field"><input type="checkbox" name="entity_{entity.id}" value="1"> '
            f'<b>{_e(effective_display_name(entity))}</b> <span class="muted">{_e(entity.ha_entity_id)} · {_e(name)} · {_e(entity.ha_domain)} · {_e(entity.area_name or "—")}</span></label>'
            for entity, name in visible_uncategorized
        )
        or '<p class="muted">Nessuna entità corrispondente.</p>'
    )
    unassigned_panel = f'''<details class="card"><summary><b>Entità non assegnate — {len(uncategorized)}</b> (visualizzate {len(visible_uncategorized)})</summary>
<form method="get"><input name="uq" placeholder="Cerca" value="{_e(request.query_params.get("uq", ""))}"><select name="uinstallation"><option value="">Tutte le installazioni</option>{installation_options}</select><select name="udomain"><option value="">Tutti i domini</option>{_options({e.ha_domain for e, _ in uncategorized}, ud)}</select><select name="uarea"><option value="">Tutte le aree</option>{_options({e.area_name for e, _ in uncategorized if e.area_name}, ua)}</select><button>Filtra non assegnate</button></form>
<form method="post" action="/voice-categories/assign-selected"><input type="hidden" name="csrf_token" value="{_e(csrf)}">{unassigned_rows}<label class="field"><b>Categoria</b><select name="target_category_id" required><option value="">Seleziona categoria</option>{category_options}</select></label><button>Assegna le entità selezionate</button></form></details>'''
    body = f'''{result}{unassigned_panel}<div class="card"><h2>Conteggio ed esempi</h2><p class="muted">Le categorie vuote non producono risultati nelle richieste di gruppo.</p></div>{category_summary}<div class="card"><h2>Classificazione automatica</h2>
<p class="muted">Assegna le categorie standard alle sole entità ancora senza categoria, usando tipo, unità, device class, nome ed entity_id. Le scelte manuali non vengono sovrascritte.</p>
<form method="post" action="/voice-categories/auto-assign"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><button>Classifica automaticamente le entità non assegnate</button></form></div>
<div class="card"><h2>Assegnazione in massa</h2>
<p class="muted">Assegna solo le entità ancora senza categoria che contengono il criterio nel nome, entity_id o device class. Esempio: battery.</p>
<form method="post" action="/voice-categories/bulk-assign"><input type="hidden" name="csrf_token" value="{_e(csrf)}">
<label class="field"><b>Criterio</b><input name="match" maxlength="80" placeholder="battery" required></label>
<label class="field"><b>Categoria di destinazione</b><select name="target_category_id" required><option value="">Seleziona categoria</option>{category_options}</select></label>
<button>Assegna tutte le corrispondenze non assegnate</button></form></div>
<div class="card"><h2>Crea categoria</h2>
<p class="muted">La categoria indica che cosa rappresenta il sensore; il nome vocale identifica il singolo sensore.</p>
<form method="post"><input type="hidden" name="csrf_token" value="{_e(csrf)}">
<label class="field"><b>Nome categoria</b><input name="name" maxlength="120" required></label>
<label class="field"><b>Codice</b><input name="slug" maxlength="64" placeholder="Generato automaticamente"></label>
<label class="field"><b>Descrizione</b><input name="description" maxlength="300"></label>
<button>Crea categoria</button></form></div>
<table><thead><tr><th>Categoria</th><th>Descrizione</th><th>Tipo</th></tr></thead><tbody>{rows}</tbody></table>'''
    response = HTMLResponse(_layout("Categorie sensori", body, context, csrf, "voice-categories"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


@router.post("/voice-categories/assign-selected", response_class=RedirectResponse)
async def assign_selected_voice_category(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _admin(context)
    values = await _form(request)
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    try:
        target_id = UUID(values.get("target_category_id", ""))
        selected_ids = [
            UUID(key.removeprefix("entity_")) for key in values if key.startswith("entity_")
        ]
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Selezione non valida") from error
    if not selected_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nessuna entità selezionata")
    if not any(item.id == target_id for item in await _voice_categories(session, context)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Categoria non trovata")
    entities = list(
        (
            await session.scalars(
                select(Entity)
                .join(Installation)
                .where(
                    Installation.tenant_id == context.tenant_id,
                    Entity.id.in_(selected_ids),
                    Entity.deleted_at.is_(None),
                    Entity.voice_category_id.is_(None),
                )
            )
        ).all()
    )
    for entity in entities:
        entity.voice_category_id = target_id
    await session.commit()
    return RedirectResponse(
        f"/voice-categories?bulk_assigned={len(entities)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/voice-categories/bulk-assign", response_class=RedirectResponse)
async def bulk_assign_voice_category(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    """Assign matching uncategorized tenant entities without overwriting choices."""
    _admin(context)
    values = await _form(request)
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    match = values.get("match", "").strip().casefold()
    if not match or len(match) > 80:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Criterio non valido")
    try:
        target_id = UUID(values.get("target_category_id", ""))
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Categoria non valida") from error
    if not any(item.id == target_id for item in await _voice_categories(session, context)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Categoria non trovata")
    entities = list(
        (
            await session.scalars(
                select(Entity)
                .join(Installation)
                .where(
                    Installation.tenant_id == context.tenant_id,
                    Entity.deleted_at.is_(None),
                    Entity.voice_category_id.is_(None),
                )
            )
        ).all()
    )
    assigned = 0
    for entity in entities:
        searchable = " ".join(
            value
            for value in (
                entity.ha_entity_id,
                entity.friendly_name,
                entity.display_name,
                entity.voice_name,
                entity.device_class,
            )
            if value
        ).casefold()
        if match in searchable:
            entity.voice_category_id = target_id
            assigned += 1
    await session.commit()
    return RedirectResponse(
        f"/voice-categories?bulk_assigned={assigned}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/voice-categories", response_class=RedirectResponse)
async def create_voice_category(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _admin(context)
    values = await _form(request)
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    name = values.get("name", "").strip()
    if not name or len(name) > 120:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nome non valido")
    slug = category_slug(values.get("slug", "") or name)
    if await session.scalar(
        select(VoiceCategory.id).where(
            VoiceCategory.tenant_id == context.tenant_id, VoiceCategory.slug == slug
        )
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Categoria già esistente")
    session.add(
        VoiceCategory(
            tenant_id=context.tenant_id,
            slug=slug,
            name=name,
            description=values.get("description", "").strip() or None,
            builtin=False,
        )
    )
    await session.commit()
    return RedirectResponse("/voice-categories", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/voice-categories/auto-assign", response_class=RedirectResponse)
async def auto_assign_voice_categories(
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    _admin(context)
    values = await _form(request)
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    categories = {item.slug: item for item in await _voice_categories(session, context)}
    entities = list(
        (
            await session.scalars(
                select(Entity)
                .join(Installation)
                .where(
                    Installation.tenant_id == context.tenant_id,
                    Entity.deleted_at.is_(None),
                    Entity.voice_category_id.is_(None),
                )
            )
        ).all()
    )
    assigned = 0
    for entity in entities:
        attributes = entity.attributes_json or {}
        slug = infer_standard_category(
            domain=entity.ha_domain,
            device_class=entity.device_class,
            unit=str(attributes.get("unit_of_measurement") or "") or None,
            names=tuple(
                value
                for value in (
                    entity.voice_name,
                    entity.display_name,
                    entity.friendly_name,
                    entity.ha_entity_id,
                    *(entity.voice_aliases or []),
                )
                if value
            ),
        )
        category = categories.get(slug or "")
        if category is not None:
            entity.voice_category_id = category.id
            assigned += 1
    await session.commit()
    return RedirectResponse(
        f"/voice-categories?assigned={assigned}", status_code=status.HTTP_303_SEE_OTHER
    )


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
    result_rows = result.all()
    for item, _entity_count in result_rows:
        overlay_installation(item, await load_live_installation(item.public_id))
    rows = "".join(
        f'<tr><td><a href="/installations/{item.id}">{_e(item.name)}</a></td><td class="{"ok" if _online(item) else "bad"}">{"online" if _online(item) else "offline"}</td><td>{_e(item.ha_version)}</td><td>{_e(item.connector_version)}</td><td>{_compatibility_badge(item)}</td><td>{entity_count}</td><td>{_e(item.last_seen_at)}</td></tr>'
        for item, entity_count in result_rows
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
    overlay_installation(item, await load_live_installation(item.public_id))
    q, domain, area = (request.query_params.get(key, "").strip() for key in ("q", "domain", "area"))
    total_entity_count = int(
        await session.scalar(
            select(func.count(Entity.id)).where(
                Entity.installation_id == item.id,
                Entity.deleted_at.is_(None),
            )
        )
        or 0
    )
    query = (
        select(Entity)
        .options(selectinload(Entity.voice_category))
        .where(
            Entity.installation_id == item.id,
            Entity.deleted_at.is_(None),
        )
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
            )
        ).all()
    )
    overlay_entities(entities, await load_live_states(item.public_id))
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
    entity_groups = _entity_groups(item, entities, csrf)
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
    inventory_sync_status = request.query_params.get("inventory_sync", "")
    inventory_sync_count = request.query_params.get("imported", "0")
    inventory_sync_notice = (
        f'<p class="ok">Entità aggiornate dal vivo. Nuove entità importate: {_e(inventory_sync_count)}.</p>'
        if inventory_sync_status == "success"
        else ""
    )
    inventory_sync_form = (
        f'<form method="post" action="/installations/{item.id}/laboratory/sync" class="actions"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><button>Aggiorna entità dal vivo</button></form>'
        if get_settings().environment == "laboratory"
        else ""
    )
    entity_content = entity_groups or '<div class="card"><p>Nessuna entità</p></div>'
    body = f'<div class="cards"><div class="card"><b>{"online" if _online(item) else "offline"}</b><br>Connessione</div><div class="card"><b>{_e(item.sync_revision)}</b><br>Revisione inventario</div><div class="card"><b>{_e(item.inventory_synced_at)}</b><br>Ultima sincronizzazione</div><div class="card"><b>{len(entities)} / {total_entity_count}</b><br>Entità visualizzate / totali</div></div>{_connector_compatibility_card(item)}{inventory_sync_notice}{inventory_sync_form}{resync_notice}{resync_form}{_alexa_discovery_section(discovery, proactive_events, list(current_alexa.values()))}<form method="get"><input name="q" placeholder="Cerca" value="{_e(q)}"><input name="domain" placeholder="Dominio" value="{_e(domain)}"><input name="area" placeholder="Area" value="{_e(area)}"><button>Filtra</button></form><section aria-label="Entità per tipo">{entity_content}</section>'
    response = HTMLResponse(_layout(item.name, body, context, csrf, "installations"))
    response.set_cookie(
        CSRF_COOKIE, csrf, secure=True, httponly=True, samesite="lax", path="/", max_age=1800
    )
    return response


@router.post(
    "/installations/{installation_id}/laboratory/sync",
    response_class=RedirectResponse,
)
async def sync_laboratory_inventory(
    installation_id: UUID,
    request: Request,
    context: Annotated[TenantContext, console_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> RedirectResponse:
    """Import the live inventory without ever writing to production."""
    _admin(context)
    if get_settings().environment != "laboratory":
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    installation = await _installation(session, context, installation_id)
    values = await _form(request)
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    imported = await sync_live_entities(session, installation)
    return RedirectResponse(
        f"/installations/{installation.id}?inventory_sync=success&imported={imported}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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


def _entity_row(
    installation: Installation,
    entity: Entity,
    csrf: str,
    media_players: list[Entity],
) -> str:
    operations = sorted(DOMAIN_OPERATIONS.get(entity.ha_domain, ()))
    enabled = bool(
        entity.deleted_at is None and entity.available and entity.ha_registry_id and operations
    )
    controls = _entity_controls(installation, entity, csrf, enabled, media_players)
    voice_name = effective_voice_name(entity)
    display_name = effective_display_name(entity)
    category_name = (
        entity.voice_category.name if entity.voice_category is not None else "Non assegnata"
    )
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
    return f'<tr class="state-{state_class}" data-entity-row="{entity.id}"><td><div class="entity-summary">{icon}<div class="entity-meta"><span class="voice-label">Nome vocale: {_e(voice_name)}</span><br><b>{_e(voice_name)}</b><br><span class="muted">Nome visualizzato: {_e(display_name)}</span><br><span class="muted">Nome e-Control: {_e(entity.friendly_name or entity.ha_entity_id)}</span><br><span class="muted">entity_id: {_e(entity.ha_entity_id)}</span><br><span class="muted">Alias: {aliases}</span></div></div></td><td><span class="badge">{_e(category_name)}</span></td><td>{_e(entity.ha_domain)} / {_e(entity.area_name or "—")}</td><td><span class="status-dot state-{state_class}"></span><span class="entity-state">{_e(lifecycle)}</span><br><span class="muted">{availability}</span></td><td><div class="direct-controls">{controls}{edit}<span class="command-feedback" role="status" aria-live="polite"></span></div></td></tr>'


ENTITY_DOMAIN_LABELS = {
    "sensor": "Sensori",
    "binary_sensor": "Sensori binari",
    "light": "Luci",
    "switch": "Interruttori",
    "cover": "Tapparelle e tende",
    "climate": "Clima",
    "fan": "Ventilazione",
    "scene": "Scenari",
    "script": "Script",
    "button": "Pulsanti",
    "alarm_control_panel": "Allarmi",
    "lock": "Serrature",
    "select": "Selettori",
    "vacuum": "Aspirapolvere",
    "valve": "Valvole",
    "water_heater": "Scaldacqua",
    "humidifier": "Umidificatori",
    "camera": "Telecamere",
}


def _entity_groups(installation: Installation, entities: list[Entity], csrf: str) -> str:
    grouped: dict[str, list[Entity]] = {}
    for entity in entities:
        grouped.setdefault(entity.ha_domain, []).append(entity)
    sections: list[str] = []
    media_players = [entity for entity in entities if entity.ha_domain == "media_player"]
    for domain in sorted(
        set(grouped) | set(ENTITY_DOMAIN_LABELS),
        key=lambda value: (ENTITY_DOMAIN_LABELS.get(value, value).casefold(), value),
    ):
        domain_entities = grouped.get(domain, [])
        rows = "".join(
            _entity_row(installation, entity, csrf, media_players) for entity in domain_entities
        )
        if not rows:
            rows = '<tr><td colspan="5" class="muted">Nessuna entitÃ  sincronizzata.</td></tr>'
        label = ENTITY_DOMAIN_LABELS.get(domain, domain.replace("_", " ").title())
        sections.append(
            f'<details class="entity-group" data-domain="{_e(domain)}">'
            f"<summary><span>{_e(label)}</span>"
            f'<span class="entity-group-count">{len(domain_entities)}</span></summary>'
            "<table><thead><tr><th>Entità</th><th>Categoria vocale</th><th>Dominio/area</th><th>Stato</th>"
            f"<th>Comandi diretti</th></tr></thead><tbody>{rows}</tbody></table></details>"
        )
    return "".join(sections)


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


def _media_player_controls(
    installation: Installation,
    entity: Entity,
    csrf: str,
    enabled: bool,
    media_players: list[Entity],
) -> str:
    features = entity.supported_features
    controls: list[str] = []
    definitions = (
        (MEDIA_PLAYER_FEATURE_TURN_ON, "power_on", "ON"),
        (MEDIA_PLAYER_FEATURE_TURN_OFF, "power_off", "OFF"),
        (MEDIA_PLAYER_FEATURE_PLAY, "media_play", "PLAY"),
        (MEDIA_PLAYER_FEATURE_PAUSE, "media_pause", "PAUSA"),
        (MEDIA_PLAYER_FEATURE_STOP, "media_stop", "STOP"),
        (MEDIA_PLAYER_FEATURE_PREVIOUS_TRACK, "media_previous", "PRECEDENTE"),
        (MEDIA_PLAYER_FEATURE_NEXT_TRACK, "media_next", "SUCCESSIVO"),
    )
    for feature, operation, label in definitions:
        if features & feature:
            controls.append(
                _control_form(
                    installation,
                    entity,
                    csrf,
                    operation,
                    label,
                    power=("on" if operation == "power_on" else "off")
                    if operation in {"power_on", "power_off"}
                    else None,
                    active=(operation == "power_on" and entity.state not in {"off", "unavailable"})
                    or (operation == "power_off" and entity.state == "off"),
                    enabled=enabled,
                )
            )
    if features & MEDIA_PLAYER_FEATURE_VOLUME_SET:
        raw_volume = _finite_number((entity.attributes_json or {}).get("volume_level"))
        volume = min(100, max(0, round((raw_volume or 0) * 100)))
        disabled = "" if enabled else " disabled"
        controls.append(
            f'<form class="entity-command inline level-control" method="post" action="/installations/{installation.id}/commands"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="entity_id" value="{entity.id}"><input type="hidden" name="operation" value="set_volume"><input type="range" name="value" min="0" max="100" value="{volume}" step="1" aria-label="Volume percentuale"{disabled}><output class="level-value">{volume}%</output><button class="command-button"{disabled}>IMPOSTA VOLUME</button></form>'
        )
    if features & MEDIA_PLAYER_FEATURE_VOLUME_MUTE:
        muted = (entity.attributes_json or {}).get("is_volume_muted") is True
        controls.append(
            _control_form(
                installation,
                entity,
                csrf,
                "volume_unmute" if muted else "volume_mute",
                "RIATTIVA AUDIO" if muted else "MUTO",
                enabled=enabled,
            )
        )
    sources = _media_sources(entity)
    if features & MEDIA_PLAYER_FEATURE_SELECT_SOURCE and sources:
        enabled_sources = [
            source
            for source in sources
            if _media_source_setting(entity, source).get("enabled", True) is not False
        ]
        if enabled_sources:
            current = (entity.attributes_json or {}).get("source")
            options = "".join(
                f'<option value="{_e(source)}"{" selected" if source == current else ""}>{_e(str(_media_source_setting(entity, source).get("name") or source))}</option>'
                for source in enabled_sources
            )
            disabled = "" if enabled else " disabled"
            controls.append(
                f'<form class="entity-command inline media-source-control" method="post" action="/installations/{installation.id}/commands"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="entity_id" value="{entity.id}"><input type="hidden" name="operation" value="select_source"><label>Fonte <select name="value"{disabled}>{options}</select></label><button class="command-button"{disabled}>SELEZIONA FONTE</button></form>'
            )
    attributes = entity.attributes_json or {}
    title = attributes.get("media_title")
    artist = attributes.get("media_artist")
    album = attributes.get("media_album_name")
    if any(isinstance(value, str) and value for value in (title, artist, album)):
        details = " · ".join(
            _e(value) for value in (title, artist, album) if isinstance(value, str) and value
        )
        controls.append(f'<div class="media-now-playing"><b>In riproduzione:</b> {details}</div>')
    controls.append(
        _control_form(
            installation,
            entity,
            csrf,
            "media_artwork",
            "MOSTRA COPERTINA",
            enabled=enabled,
        )
        + '<img class="command-image-preview media-artwork-preview" hidden alt="Copertina del contenuto in riproduzione" style="max-width:360px;width:100%;height:auto;border-radius:8px">'
    )
    if features & MEDIA_PLAYER_FEATURE_GROUPING:
        candidates = [
            item
            for item in media_players
            if item.id != entity.id
            and item.ha_registry_id
            and item.deleted_at is None
            and item.available
        ]
        if candidates:
            options = "".join(
                f'<option value="{_e(item.ha_registry_id)}">{_e(effective_display_name(item))}</option>'
                for item in candidates
            )
            disabled = "" if enabled else " disabled"
            controls.append(
                f'<form class="entity-command inline" method="post" action="/installations/{installation.id}/commands"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="entity_id" value="{entity.id}"><input type="hidden" name="operation" value="media_join"><label>Aggiungi stanza <select name="value"{disabled}>{options}</select></label><button class="command-button"{disabled}>AGGIUNGI</button></form>'
            )
        group_members = attributes.get("group_members")
        if isinstance(group_members, list) and len(group_members) > 1:
            grouped_players = [
                item
                for item in media_players
                if item.ha_entity_id in group_members
                and (_finite_number((item.attributes_json or {}).get("volume_level")) is not None)
            ]
            grouped_volumes = [
                float((item.attributes_json or {})["volume_level"]) for item in grouped_players
            ]
            if grouped_volumes:
                group_volume = min(
                    100, max(0, round(sum(grouped_volumes) * 100 / len(grouped_volumes)))
                )
                disabled = "" if enabled else " disabled"
                controls.append(
                    f'<form class="entity-command inline level-control group-volume-control" method="post" action="/installations/{installation.id}/commands"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="entity_id" value="{entity.id}"><input type="hidden" name="operation" value="set_group_volume"><label>Volume generale <input type="range" name="value" min="0" max="100" value="{group_volume}" step="1" aria-label="Volume generale del gruppo"{disabled}></label><output class="level-value">{group_volume}%</output><button class="command-button"{disabled}>IMPOSTA GRUPPO</button></form>'
                )
            controls.append(
                _control_form(
                    installation,
                    entity,
                    csrf,
                    "media_unjoin",
                    "RIMUOVI DAL GRUPPO",
                    enabled=enabled,
                )
            )
    return "".join(controls) or '<span class="muted">Nessun controllo supportato</span>'


def _range_command_form(
    installation: Installation,
    entity: Entity,
    csrf: str,
    operation: str,
    label: str,
    value: float,
    minimum: float,
    maximum: float,
    enabled: bool,
    *,
    step: str = "1",
) -> str:
    disabled = "" if enabled else " disabled"
    return f'<form class="entity-command inline level-control" method="post" action="/installations/{installation.id}/commands"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="entity_id" value="{entity.id}"><input type="hidden" name="operation" value="{_e(operation)}"><label>{_e(label)} <input type="number" name="value" value="{_e(value)}" min="{_e(minimum)}" max="{_e(maximum)}" step="{step}"{disabled}></label><button class="command-button"{disabled}>IMPOSTA</button></form>'


def _option_command_form(
    installation: Installation,
    entity: Entity,
    csrf: str,
    operation: str,
    label: str,
    values: list[object],
    current: object,
    enabled: bool,
) -> str:
    options = "".join(
        f'<option value="{_e(value)}"{" selected" if value == current else ""}>{_e(value)}</option>'
        for value in values
        if isinstance(value, str)
    )
    if not options:
        return '<span class="muted">Nessun controllo diretto</span>'
    disabled = "" if enabled else " disabled"
    return f'<form class="entity-command inline" method="post" action="/installations/{installation.id}/commands"><input type="hidden" name="csrf_token" value="{_e(csrf)}"><input type="hidden" name="entity_id" value="{entity.id}"><input type="hidden" name="operation" value="{_e(operation)}"><label>{_e(label)} <select name="value"{disabled}>{options}</select></label><button class="command-button"{disabled}>SELEZIONA</button></form>'


def _entity_controls(
    installation: Installation,
    entity: Entity,
    csrf: str,
    enabled: bool,
    media_players: list[Entity],
) -> str:
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
    if entity.ha_domain == "media_player":
        return _media_player_controls(installation, entity, csrf, enabled, media_players)
    if entity.ha_domain == "fan":
        attributes = entity.attributes_json or {}
        controls = [
            _control_form(installation, entity, csrf, "power_on", "ON", enabled=enabled),
            _control_form(installation, entity, csrf, "power_off", "OFF", enabled=enabled),
        ]
        if entity.supported_features & FAN_FEATURE_SET_SPEED:
            controls.append(
                _range_command_form(
                    installation,
                    entity,
                    csrf,
                    "set_percentage",
                    "Velocità",
                    _finite_number(attributes.get("percentage")) or 0,
                    0,
                    100,
                    enabled,
                )
            )
        presets = attributes.get("preset_modes")
        if entity.supported_features & FAN_FEATURE_PRESET_MODE and isinstance(presets, list):
            controls.append(
                _option_command_form(
                    installation,
                    entity,
                    csrf,
                    "set_preset_mode",
                    "Modalità",
                    presets,
                    attributes.get("preset_mode"),
                    enabled,
                )
            )
        if entity.supported_features & FAN_FEATURE_OSCILLATE:
            oscillating = attributes.get("oscillating") is True
            controls.append(
                _control_form(
                    installation,
                    entity,
                    csrf,
                    "oscillate_off" if oscillating else "oscillate_on",
                    "FERMA OSCILLAZIONE" if oscillating else "OSCILLA",
                    enabled=enabled,
                )
            )
        if entity.supported_features & FAN_FEATURE_DIRECTION:
            forward = attributes.get("direction") == "forward"
            controls.append(
                _control_form(
                    installation,
                    entity,
                    csrf,
                    "direction_reverse" if forward else "direction_forward",
                    "DIREZIONE INDIETRO" if forward else "DIREZIONE AVANTI",
                    enabled=enabled,
                )
            )
        return "".join(controls)
    if entity.ha_domain == "select":
        options = (entity.attributes_json or {}).get("options", [])
        if isinstance(options, list) and options:
            return _option_command_form(
                installation,
                entity,
                csrf,
                "select_option",
                "Opzione",
                options,
                entity.state,
                enabled,
            )
    if entity.ha_domain == "water_heater":
        attributes = entity.attributes_json or {}
        controls = [
            _control_form(installation, entity, csrf, "power_on", "ON", enabled=enabled),
            _control_form(installation, entity, csrf, "power_off", "OFF", enabled=enabled),
        ]
        current = _finite_number(attributes.get("temperature"))
        minimum = _finite_number(attributes.get("min_temp"))
        maximum = _finite_number(attributes.get("max_temp"))
        if current is not None and minimum is not None and maximum is not None:
            controls.append(
                _range_command_form(
                    installation,
                    entity,
                    csrf,
                    "set_target_temperature",
                    "Temperatura",
                    current,
                    minimum,
                    maximum,
                    enabled,
                    step="0.5",
                )
            )
        return "".join(controls)
    if entity.ha_domain == "humidifier":
        attributes = entity.attributes_json or {}
        controls = [
            _control_form(installation, entity, csrf, "power_on", "ON", enabled=enabled),
            _control_form(installation, entity, csrf, "power_off", "OFF", enabled=enabled),
        ]
        controls.append(
            _range_command_form(
                installation,
                entity,
                csrf,
                "set_percentage",
                "Umidità",
                _finite_number(attributes.get("humidity")) or 0,
                _finite_number(attributes.get("min_humidity")) or 0,
                _finite_number(attributes.get("max_humidity")) or 100,
                enabled,
            )
        )
        modes = attributes.get("available_modes")
        if isinstance(modes, list):
            controls.append(
                _option_command_form(
                    installation,
                    entity,
                    csrf,
                    "set_mode",
                    "Modalità",
                    modes,
                    attributes.get("mode"),
                    enabled,
                )
            )
        return "".join(controls)
    if entity.ha_domain == "camera":
        return (
            _control_form(
                installation,
                entity,
                csrf,
                "camera_snapshot",
                "AGGIORNA FOTO",
                enabled=enabled,
            )
            + '<img class="command-image-preview camera-preview" hidden alt="Anteprima telecamera" style="max-width:640px;width:100%;height:auto;border-radius:8px">'
        )
    simple_labels = {
        "power_on": "ON",
        "power_off": "OFF",
        "open": "APRI",
        "close": "CHIUDI",
        "stop": "STOP",
        "activate": "ATTIVA",
        "press": "PREMI",
        "lock": "BLOCCA",
        "unlock": "SBLOCCA",
        "arm_home": "INSERISCI CASA",
        "arm_away": "INSERISCI FUORI CASA",
        "disarm": "DISINSERISCI",
        "start": "AVVIA",
        "return_to_base": "TORNA ALLA BASE",
        "oscillate_on": "OSCILLA",
        "oscillate_off": "FERMA OSCILLAZIONE",
        "direction_forward": "DIREZIONE AVANTI",
        "direction_reverse": "DIREZIONE INDIETRO",
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
    categories: list[VoiceCategory],
    *,
    message: str = "",
    error: bool = False,
) -> str:
    aliases = "\n".join(entity.voice_aliases or [])
    category_options = '<option value="">Nessuna categoria</option>' + "".join(
        f'<option value="{item.id}"{" selected" if item.id == entity.voice_category_id else ""}>{_e(item.name)}</option>'
        for item in categories
    )
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
    media_sources = ""
    if entity.ha_domain == "media_player" and _media_sources(entity):
        rows: list[str] = []
        for index, source in enumerate(_media_sources(entity)):
            setting = _media_source_setting(entity, source)
            checked = " checked" if setting.get("enabled", True) is not False else ""
            aliases_value = setting.get("aliases", [])
            source_aliases = (
                "\n".join(item for item in aliases_value if isinstance(item, str))
                if isinstance(aliases_value, list)
                else ""
            )
            rows.append(
                f'<fieldset class="media-source-setting"><legend>{_e(source)}</legend><input type="hidden" name="media_source_{index}" value="{_e(source)}"><label><input type="checkbox" name="media_source_enabled_{index}" value="1"{checked}> Abilitata nel portale e per Alexa</label><label class="field">Nome vocale fonte<input name="media_source_name_{index}" maxlength="120" value="{_e(str(setting.get("name") or ""))}" placeholder="{_e(source)}"></label><label class="field">Alias fonte<textarea name="media_source_aliases_{index}" maxlength="2420" placeholder="Un alias per riga">{_e(source_aliases)}</textarea></label></fieldset>'
            )
        media_sources = (
            '<h3>Fonti Media Player</h3><p class="muted">Le fonti arrivano da Home Assistant. Puoi escluderle o assegnare nomi e alias vocali senza modificare il nome tecnico.</p>'
            + "".join(rows)
        )
    return f'''{notice}<div class="card"><p><b>Nome e-Control</b><br>{_e(entity.friendly_name or entity.ha_entity_id)}<br><span class="muted">Sincronizzato automaticamente e non modificabile qui.</span></p>
<form method="post"><input type="hidden" name="csrf_token" value="{_e(csrf)}">
<label class="field"><b>Nome visualizzato</b><input name="display_name" maxlength="120" value="{_e(entity.display_name)}" placeholder="Fallback: {_e(entity.friendly_name or entity.ha_entity_id)}"><span class="muted">Se vuoto: Nome e-Control.</span></label>
<label class="field"><b>Nome vocale</b><input name="voice_name" maxlength="120" value="{_e(entity.voice_name)}" placeholder="Fallback: {_e(effective_display_name(entity))}"><span class="muted">Se vuoto: Nome visualizzato → Nome e-Control.</span></label>
<label class="field"><b>Alias vocali</b><textarea name="voice_aliases" maxlength="2420" placeholder="Un alias per riga">{_e(aliases)}</textarea><span class="muted">Massimo 20 alias; spazi e duplicati senza distinzione maiuscole/minuscole vengono normalizzati.</span></label>
<label class="field"><b>Categoria vocale</b><select name="voice_category_id">{category_options}</select><span class="muted">Indica a Ekonex se il sensore rappresenta produzione, consumo, batteria, temperatura, allarme, serratura o altro.</span></label>
{device_type}
{cover_mode}
{media_sources}
<p><b>Nome dashboard effettivo:</b> {_e(effective_display_name(entity))}<br><b>Nome vocale effettivo:</b> {_e(effective_voice_name(entity))}<br><b>Tutti i nomi vocali:</b> {_e(", ".join(all_voice_names(entity)))}</p>
<div class="actions"><button name="action" value="save">Salva</button><a class="button" href="/installations/{installation.id}">Annulla</a><button class="danger" name="action" value="reset">Ripristina nomi personalizzati</button></div></form></div>'''


def _names_page(
    installation: Installation,
    entity: Entity,
    context: TenantContext,
    csrf: str,
    categories: list[VoiceCategory],
    *,
    message: str = "",
    error: bool = False,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    response = HTMLResponse(
        _layout(
            "Modifica nomi entità",
            _entity_names_form(
                installation, entity, csrf, categories, message=message, error=error
            ),
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
    return _names_page(
        installation, entity, context, csrf, await _voice_categories(session, context)
    )


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
    categories = await _voice_categories(session, context)
    values = await _form(request)
    if not _valid_csrf(values.get("csrf_token", ""), request.cookies.get(CSRF_COOKIE), context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Richiesta non valida")
    previous = (
        entity.display_name,
        entity.voice_name,
        list(entity.voice_aliases or []),
        entity.alexa_cover_mode,
        entity.alexa_device_type,
        entity.voice_category_id,
        dict(entity.media_source_settings or {}),
    )
    try:
        if values.get("action") == "reset":
            entity.display_name, entity.voice_name, entity.voice_aliases = None, None, []
            entity.media_source_settings = {}
        else:
            entity.display_name = clean_optional_name(values.get("display_name", ""))
            entity.voice_name = clean_optional_name(values.get("voice_name", ""))
            entity.voice_aliases = clean_voice_aliases(
                re.split(r"[\r\n,]+", values.get("voice_aliases", ""))
            )
            requested_category = values.get("voice_category_id", "")
            entity.voice_category_id = UUID(requested_category) if requested_category else None
            if entity.voice_category_id is not None and not any(
                item.id == entity.voice_category_id for item in categories
            ):
                raise ValueError("unknown category")
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
            if entity.ha_domain == "media_player":
                source_settings: dict[str, object] = {}
                for index, source in enumerate(_media_sources(entity)):
                    source_settings[source] = {
                        "enabled": values.get(f"media_source_enabled_{index}") == "1",
                        "name": clean_optional_name(values.get(f"media_source_name_{index}", "")),
                        "aliases": clean_voice_aliases(
                            re.split(
                                r"[\r\n,]+",
                                values.get(f"media_source_aliases_{index}", ""),
                            )
                        ),
                    }
                entity.media_source_settings = source_settings
    except ValueError:
        (
            entity.display_name,
            entity.voice_name,
            entity.voice_aliases,
            entity.alexa_cover_mode,
            entity.alexa_device_type,
            entity.voice_category_id,
            entity.media_source_settings,
        ) = previous
        return _names_page(
            installation,
            entity,
            context,
            _csrf(context),
            categories,
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
            entity.voice_category_id,
            entity.media_source_settings,
        ) = previous
        return _names_page(
            installation,
            entity,
            context,
            _csrf(context),
            categories,
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
        entity.voice_category_id,
        dict(entity.media_source_settings or {}),
    )
    changed_fields = [
        name
        for name, before, after in zip(
            (
                "display_name",
                "voice_name",
                "voice_aliases",
                "alexa_cover_mode",
                "alexa_device_type",
                "voice_category_id",
                "media_source_settings",
            ),
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
    if get_settings().environment != "laboratory":
        await reconcile_discovery_safely(session, installation)
    return _names_page(
        installation,
        entity,
        context,
        _csrf(context),
        categories,
        message="Nomi salvati. Categoria aggiornata.",
    )


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
        data["percentage"] = _integer_form_value(value)
    elif operation == "set_volume":
        data["volume_percent"] = int(value)
    elif operation == "set_group_volume":
        data["volume_percent"] = int(value)
    elif operation == "select_source":
        data["source"] = value
    elif operation == "media_join":
        data["member_registry_id"] = value
    elif operation == "select_option":
        data["option"] = value
    elif operation == "set_preset_mode":
        data["preset_mode"] = value
    elif operation == "set_mode":
        data["mode"] = value
    return data


def _integer_form_value(value: str) -> int:
    """Parse an integer emitted by a localized HTML number input."""
    number = float(value.strip().replace(",", "."))
    if not number.is_integer():
        raise ValueError("integer value required")
    return int(number)


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


def _validate_domain_command_value(entity: Entity, operation: str, value: str) -> None:
    attributes = entity.attributes_json or {}
    if operation == "select_option" and value not in attributes.get("options", []):
        raise ValueError("select option not advertised by entity")
    if operation == "set_preset_mode" and value not in attributes.get("preset_modes", []):
        raise ValueError("fan preset mode not advertised by entity")
    if operation == "set_mode" and value not in attributes.get("available_modes", []):
        raise ValueError("humidifier mode not advertised by entity")
    if entity.ha_domain == "water_heater" and operation == "set_target_temperature":
        requested = float(value)
        minimum = _finite_number(attributes.get("min_temp"))
        maximum = _finite_number(attributes.get("max_temp"))
        if minimum is None or maximum is None or not minimum <= requested <= maximum:
            raise ValueError("water heater temperature outside bounds")
    if entity.ha_domain == "humidifier" and operation == "set_percentage":
        requested = _integer_form_value(value)
        minimum = int(_finite_number(attributes.get("min_humidity")) or 0)
        maximum = int(_finite_number(attributes.get("max_humidity")) or 100)
        if not minimum <= requested <= maximum:
            raise ValueError("humidifier target outside bounds")


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
        _validate_domain_command_value(
            entity,
            values.get("operation", ""),
            values.get("value", ""),
        )
        if values.get("operation") == "select_source":
            source = values.get("value", "")
            if (
                source not in _media_sources(entity)
                or _media_source_setting(entity, source).get("enabled", True) is False
            ):
                raise ValueError("media source is unavailable or disabled")
        if values.get("operation") == "media_join":
            member_registry_id = values.get("value", "")
            member = await session.scalar(
                select(Entity).where(
                    Entity.installation_id == installation.id,
                    Entity.ha_registry_id == member_registry_id,
                    Entity.ha_domain == "media_player",
                    Entity.deleted_at.is_(None),
                    Entity.available.is_(True),
                )
            )
            if member is None or member.id == entity.id:
                raise ValueError("media group member is unavailable")
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
            "message": (
                "Comando eseguito"
                if succeeded
                else f"Comando non riuscito: {outcome.error_code or outcome.status}"
            ),
            "status": outcome.status,
            "error_code": outcome.error_code,
            "state": target_state,
        }
        if succeeded and outcome.response_data:
            content_type = outcome.response_data.get("content_type", "image/jpeg")
            image_base64 = outcome.response_data.get("image_base64")
            if image_base64:
                response_payload["image_data_url"] = f"data:{content_type};base64,{image_base64}"
        if succeeded and command.operation in {"set_target_temperature", "set_hvac_mode"}:
            command_payload = command.model_dump(mode="json")
            response_payload["value"] = command_payload.get(
                "temperature" if command.operation == "set_target_temperature" else "hvac_mode"
            )
        return JSONResponse(
            response_payload,
            # Keep command outcomes as application-level JSON so reverse proxies do not
            # replace the useful diagnostic with their generic 502 error page.
            status_code=status.HTTP_200_OK,
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
