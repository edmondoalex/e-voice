# ruff: noqa: E501
"""HTTP contract for official installation-bound Wear devices."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .command_dispatch import CommandDispatchService, PowerCommand
from .conversation_service import ConversationEntityService
from .database import get_database_session
from .domain.models import Entity, Installation, WearDevice, WearEnrollment
from .evcp import LIVENESS_TIMEOUT_SECONDS, sessions
from .installation_bound import BoundContext
from .installation_bound_api import _frame_policy, _ui_csrf, bound_context_dependency
from .wear_devices import CAPABILITIES, WearClaimLimiter, WearDenied, WearDeviceService
from .wear_notifications import WearNotificationService

router = APIRouter(tags=["wear"])
session_dependency = Depends(get_database_session)


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: set[str] = Field(default_factory=lambda: set(CAPABILITIES))


class EnrollmentResponse(BaseModel):
    enrollment_id: str
    code: str
    expires_at: datetime


class EnrollmentStatusResponse(BaseModel):
    claimed: bool
    device_name: str | None = None


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(pattern=r"^\d{6}$")
    display_name: str = Field(min_length=1, max_length=120)


class ClaimResponse(BaseModel):
    device_id: str
    credential: str


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    utterance: str = Field(min_length=1, max_length=300)


def _voice_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    words = re.findall(r"[a-z0-9]+", "".join(c for c in normalized if not unicodedata.combining(c)))
    aliases = {"luci": "luce", "lampade": "luce", "lampada": "luce"}
    return {
        aliases.get(word, word)
        for word in words
        if word not in {"la", "le", "il", "i", "lo", "gli"}
    }


async def _wear_power_command(session: AsyncSession, context, utterance: str) -> str | None:
    words = _voice_tokens(utterance)
    operation = "power_on" if "accendi" in words else "power_off" if "spegni" in words else None
    if operation is None:
        return None
    context.require("command.execute")
    target = words - {"accendi", "spegni", "luce"}
    if not target:
        return "Dimmi quale dispositivo vuoi comandare."
    entities = list(
        (
            await session.scalars(
                select(Entity).where(
                    Entity.installation_id == context.installation_id,
                    Entity.deleted_at.is_(None),
                    Entity.available.is_(True),
                    Entity.ha_domain.in_(("light", "switch")),
                )
            )
        ).all()
    )
    ranked = []
    for entity in entities:
        names = (entity.voice_name, entity.display_name, entity.friendly_name)
        score = max(
            (len(target & (_voice_tokens(name) - {"luce"})) for name in names if name), default=0
        )
        if score:
            ranked.append((score, entity))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        return "Non trovo un solo dispositivo autorizzato con quel nome."
    entity = ranked[0][1]
    if not entity.ha_registry_id:
        return "Il dispositivo non dispone di un identificativo di comando."
    outcome = await CommandDispatchService(session, sessions).dispatch(
        context.installation_id, entity.ha_registry_id, PowerCommand(operation=operation)
    )
    await session.commit()
    if outcome.status != "success":
        return "Il comando non è riuscito."
    action = "acceso" if operation == "power_on" else "spento"
    return f"Ho {action} {entity.voice_name or entity.display_name or entity.friendly_name}."


class DeviceCapabilitiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: set[str]


class TestNotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="Notifica di prova Ekonex", min_length=1, max_length=120)
    body: str = Field(
        default="Le notifiche sul tuo orologio funzionano.", min_length=1, max_length=500
    )


NOTIFICATION_CATEGORIES = ("security", "climate", "energy", "routines", "maintenance", "system")


class NotificationPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    categories: set[str]
    minimum_severity: str = Field(pattern=r"^(info|warning|critical)$")
    vibration: bool
    quiet_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="Europe/Rome", pattern=r"^[A-Za-z_]+(?:/[A-Za-z_+-]+)+$")


QUICK_ACTION_ICONS = {"light", "power", "climate", "blind", "lock", "scene", "music"}
QUICK_ACTION_COLORS = {"red", "blue", "green", "orange", "purple", "cyan"}


class QuickActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    slot: int = Field(ge=1, le=6)
    label: str = Field(min_length=1, max_length=24)
    utterance: str = Field(min_length=1, max_length=160)
    icon: str = Field(default="scene")
    color: str = Field(default="blue")
    confirmation: bool = False

    @field_validator("icon")
    @classmethod
    def valid_icon(cls, value: str) -> str:
        if value not in QUICK_ACTION_ICONS:
            raise ValueError("invalid_quick_action_icon")
        return value

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        if value not in QUICK_ACTION_COLORS:
            raise ValueError("invalid_quick_action_color")
        return value


class QuickActionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[QuickActionRequest] = Field(max_length=6)

    @field_validator("actions")
    @classmethod
    def unique_slots(cls, value: list[QuickActionRequest]) -> list[QuickActionRequest]:
        if len({item.slot for item in value}) != len(value):
            raise ValueError("duplicate_quick_action_slot")
        return value


async def _wear_context(
    session: Annotated[AsyncSession, session_dependency],
    authorization: Annotated[str | None, Header()] = None,
):
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wear_credential_denied")
    try:
        context = await WearDeviceService(session).context(authorization[7:])
    except WearDenied as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wear_credential_denied") from error
    return context


wear_context_dependency = Depends(_wear_context)


@router.post("/bounded/wear/enrollments", response_model=EnrollmentResponse)
async def create_enrollment(
    payload: EnrollmentRequest,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    x_ekonex_csrf: Annotated[str | None, Header()] = None,
) -> EnrollmentResponse:
    context.require("use")
    if x_ekonex_csrf != _ui_csrf(context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_denied")
    try:
        enrollment, code = await WearDeviceService(session).create_enrollment(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            installation_id=context.effective_installation_id,
            created_by_user_id=context.user_id,
            capabilities=payload.capabilities,
        )
        await session.commit()
    except WearDenied as error:
        await session.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "wear_enrollment_denied") from error
    return EnrollmentResponse(
        enrollment_id=str(enrollment.id), code=code, expires_at=enrollment.expires_at
    )


@router.get("/bounded/wear/enrollments/{enrollment_id}", response_model=EnrollmentStatusResponse)
async def enrollment_status(
    enrollment_id: UUID,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> EnrollmentStatusResponse:
    context.require("use")
    enrollment = await session.scalar(
        select(WearEnrollment).where(
            WearEnrollment.id == enrollment_id,
            WearEnrollment.tenant_id == context.tenant_id,
            WearEnrollment.user_id == context.user_id,
            WearEnrollment.installation_id == context.effective_installation_id,
            WearEnrollment.revoked_at.is_(None),
        )
    )
    if enrollment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "wear_enrollment_not_found")
    if enrollment.claimed_at is None:
        return EnrollmentStatusResponse(claimed=False)
    device_name = await session.scalar(
        select(WearDevice.display_name)
        .where(
            WearDevice.tenant_id == context.tenant_id,
            WearDevice.user_id == context.user_id,
            WearDevice.installation_id == context.effective_installation_id,
            WearDevice.revoked_at.is_(None),
        )
        .order_by(WearDevice.created_at.desc())
        .limit(1)
    )
    return EnrollmentStatusResponse(claimed=True, device_name=device_name)


@router.get("/bounded/wear", response_class=HTMLResponse)
async def wear_management(
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> HTMLResponse:
    context.require("use")
    devices = (
        await session.scalars(
            select(WearDevice)
            .where(
                WearDevice.tenant_id == context.tenant_id,
                WearDevice.user_id == context.user_id,
                WearDevice.installation_id == context.effective_installation_id,
                WearDevice.revoked_at.is_(None),
            )
            .order_by(WearDevice.created_at.desc())
        )
    ).all()

    def device_card(item: WearDevice) -> str:
        preferences = item.notification_preferences_json or {}
        selected = set(preferences.get("categories") or NOTIFICATION_CATEGORIES)
        categories = "".join(
            f'<label><input type=checkbox data-category value="{value}" {"checked" if value in selected else ""}> {label}</label>'
            for value, label in (
                ("security", "Sicurezza"),
                ("climate", "Clima"),
                ("energy", "Energia"),
                ("routines", "Routine"),
                ("maintenance", "Manutenzione"),
                ("system", "Sistema"),
            )
        )
        severity = str(preferences.get("minimum_severity") or "info")
        options = "".join(
            f'<option value="{value}" {"selected" if value == severity else ""}>{label}</option>'
            for value, label in (
                ("info", "Tutte"),
                ("warning", "Avvisi e critiche"),
                ("critical", "Solo critiche"),
            )
        )
        saved_actions = {
            int(action["slot"]): action for action in preferences.get("quick_actions", [])
        }
        icon_labels = {
            "light": "Luce",
            "power": "Accensione",
            "climate": "Clima",
            "blind": "Tapparella",
            "lock": "Sicurezza",
            "scene": "Scenario",
            "music": "Musica",
        }
        action_rows = []
        for slot in range(1, 7):
            action = saved_actions.get(slot, {})
            icon = str(action.get("icon") or "scene")
            color = str(action.get("color") or "blue")
            icon_options = "".join(
                f'<option value="{value}" {"selected" if value == icon else ""}>{label}</option>'
                for value, label in icon_labels.items()
            )
            color_options = "".join(
                f'<option value="{value}" {"selected" if value == color else ""}>{label}</option>'
                for value, label in (
                    ("red", "Rosso"),
                    ("blue", "Blu"),
                    ("green", "Verde"),
                    ("orange", "Arancio"),
                    ("purple", "Viola"),
                    ("cyan", "Ciano"),
                )
            )
            action_rows.append(
                f'<div class=quick-row data-quick-slot="{slot}"><b>{slot}</b>'
                f'<input data-quick-label maxlength=24 placeholder="Etichetta" value="{html.escape(str(action.get("label") or ""))}">'
                f'<input data-quick-utterance maxlength=160 placeholder="Comando, es. Accendi luce ufficio" value="{html.escape(str(action.get("utterance") or ""))}">'
                f"<select data-quick-icon>{icon_options}</select><select data-quick-color>{color_options}</select></div>"
                f"<label class=quick-confirm><input type=checkbox data-quick-confirmation {'checked' if action.get('confirmation', False) else ''}> Richiedi conferma sul Wear</label>"
            )
        quick_actions = "".join(action_rows)
        return f'''<article class=device-card data-preferences="{item.id}"><h2>{html.escape(item.display_name)}</h2>
<p class=muted>{html.escape(", ".join(item.capabilities_json))}</p>
<details class=quick-actions open><summary>Comandi rapidi</summary><p class=muted>Configura fino a sei tasti. Lascia vuota l'etichetta per non mostrare lo slot.</p>{quick_actions}<button data-save-quick="{item.id}">Salva comandi rapidi</button></details>
<label class=switch><input type=checkbox data-enabled {"checked" if preferences.get("enabled", True) else ""}> Notifiche attive</label>
<fieldset><legend>Categorie</legend><div class=categories>{categories}</div></fieldset>
<label>Priorità minima <select data-severity>{options}</select></label>
<label class=switch><input type=checkbox data-vibration {"checked" if preferences.get("vibration", True) else ""}> Vibrazione</label>
<div class=quiet><label>Silenzioso dalle <input type=time data-quiet-start value="{html.escape(str(preferences.get("quiet_start") or ""))}"></label><label> alle <input type=time data-quiet-end value="{html.escape(str(preferences.get("quiet_end") or ""))}"></label></div>
<div class=actions><button data-save="{item.id}">Salva preferenze</button><button data-test="{item.id}">Invia prova</button><button class=danger data-device="{item.id}">Revoca</button></div></article>'''

    rows = "".join(device_card(item) for item in devices) or "<p>Nessun orologio associato.</p>"
    capabilities = ",".join(sorted(CAPABILITIES))
    page = f"""<!doctype html><html lang=it><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'><title>Google Wear</title>
<style>body{{font:16px system-ui;background:#101619;color:#f4f7f8;margin:0;padding:24px}}button,select,input{{padding:10px 14px;border:1px solid #41cbea;border-radius:10px;background:#142126;color:#fff}}button{{font-weight:700}}code{{font-size:18px;color:#41cbea}}.device-card,.installer-guide{{max-width:1050px;margin:18px 0;padding:22px;border:1px solid #314149;border-radius:18px;background:#151c20}}.device-card h2{{margin-top:0}}.installer-guide summary,.quick-actions summary{{cursor:pointer;font-size:20px;font-weight:750}}.installer-guide ol{{line-height:1.55}}.installer-guide .warning{{padding:12px;border-left:4px solid #f4b942;background:#1d2427}}.badge{{display:inline-block;margin-left:8px;padding:3px 8px;border-radius:999px;background:#29383e;color:#9ee9f4;font-size:12px;vertical-align:middle}}.muted{{color:#9db2bc}}fieldset,.quick-actions{{margin:16px 0;border:1px solid #314149;border-radius:12px;padding:14px}}.categories,.actions,.quiet{{display:flex;gap:14px;flex-wrap:wrap;align-items:center}}.categories label,.switch{{display:flex;gap:8px;align-items:center;padding:8px}}.actions{{margin-top:18px}}.quick-row{{display:grid;grid-template-columns:28px minmax(120px,1fr) minmax(240px,2fr) 130px 120px;gap:8px;align-items:center;margin:8px 0}}.danger{{border-color:#d92d20;color:#ffb4ac}}@media(max-width:760px){{.quick-row{{grid-template-columns:28px 1fr}}.quick-row input,.quick-row select{{grid-column:2}}}}</style></head><body>
<h1>Google Wear</h1><p>Orologi legati a questo utente e a questo impianto.</p>
<details class=installer-guide><summary>Installazione privata APK <span class=badge>Solo installatore</span></summary>
<p>Questa procedura serve per installazioni private prima della distribuzione tramite Google Play. Non deve essere eseguita dal cliente finale.</p>
<ol><li>Richiedi a EA SAS il pacchetto firmato <b>Ekonex Wear</b> e verifica versione e impronta SHA-256 comunicate.</li>
<li>Sull'orologio apri <b>Impostazioni &rarr; Sistema &rarr; Informazioni &rarr; Versioni</b> e tocca sette volte il numero build per abilitare le opzioni sviluppatore.</li>
<li>Apri <b>Opzioni sviluppatore &rarr; Debug wireless</b>, associa il computer dell'installatore con il codice mostrato e annota indirizzo e porta.</li>
<li>Dal computer autorizzato installa l'APK con lo strumento Ekonex per Wear. L'installazione tecnica usa il collegamento ADB wireless soltanto in questa fase.</li>
<li>Apri Ekonex Wear sull'orologio, genera qui sotto il codice monouso e completa <b>Associa</b>.</li>
<li>Verifica un comando e una notifica di prova, quindi <b>disattiva Debug wireless</b> sull'orologio.</li></ol>
<p><button id=open-installer type=button>Apri installazione tecnica</button></p>
<p class=warning><b>Sicurezza:</b> non lasciare attivo il debug wireless e non consegnare chiavi, token o credenziali ADB al cliente.</p></details>
<button id=create>Genera codice associazione</button><p id=result role=status></p>
<h2>Dispositivi attivi</h2>{rows}
<script>async function csrfToken(){{const response=await fetch('/bounded/ui/csrf',{{cache:'no-store'}});if(!response.ok)throw new Error(response.status===401||response.status===403?'session_expired':'csrf_unavailable');const value=await response.json();if(!value.csrf_token)throw new Error('csrf_unavailable');return value.csrf_token}}function actionError(error,fallback){{result.textContent=error&&error.message==='session_expired'?'Sessione scaduta: riapri Google Wear dalla barra laterale di e-Control.':fallback}}</script>
<script>document.querySelector('#open-installer').onclick=()=>parent.postMessage('ekonex:wear-installer','*');</script>
<script>const result=document.querySelector('#result');let enrollmentPoll;
async function waitForEnrollment(id){{clearInterval(enrollmentPoll);enrollmentPoll=setInterval(async()=>{{try{{const response=await fetch('/bounded/wear/enrollments/'+id,{{cache:'no-store'}});if(response.status===401||response.status===403)throw new Error('session_expired');if(!response.ok)return;const value=await response.json();if(value.claimed){{clearInterval(enrollmentPoll);const name=value.device_name?' ('+value.device_name+')':'';result.textContent='Orologio associato correttamente'+name+'. Aggiornamento dispositivi...';setTimeout(()=>location.reload(),900)}}}}catch(error){{clearInterval(enrollmentPoll);actionError(error,'Verifica associazione non disponibile')}}}},1500)}}
document.querySelector('#create').onclick=async()=>{{
try{{const csrf=await csrfToken(),response=await fetch('/bounded/wear/enrollments',{{method:'POST',headers:{{'Content-Type':'application/json','X-Ekonex-CSRF':csrf}},body:JSON.stringify({{capabilities:'{capabilities}'.split(',')}})}});if(response.status===401||response.status===403)throw new Error('session_expired');const value=await response.json();if(response.ok){{result.innerHTML=`Codice monouso (10 minuti): <code>${{value.code}}</code><br><span class=muted>In attesa dell'associazione dall'orologio...</span>`;waitForEnrollment(value.enrollment_id)}}else result.textContent='Associazione non disponibile'}}catch(error){{actionError(error,'Associazione non disponibile')}}}};</script>
<script>document.querySelectorAll('[data-device]').forEach(button=>button.onclick=async()=>{{if(!confirm('Revocare questo orologio?'))return;try{{const csrf=await csrfToken(),response=await fetch('/bounded/wear/devices/'+button.dataset.device,{{method:'DELETE',headers:{{'X-Ekonex-CSRF':csrf}}}});if(response.status===401||response.status===403)throw new Error('session_expired');if(response.ok)location.reload();else result.textContent='Revoca non riuscita'}}catch(error){{actionError(error,'Revoca non riuscita')}}}});</script>
<script>document.querySelectorAll('[data-test]').forEach(button=>button.onclick=async()=>{{try{{const csrf=await csrfToken(),response=await fetch('/bounded/wear/devices/'+button.dataset.test+'/notifications/test',{{method:'POST',headers:{{'Content-Type':'application/json','X-Ekonex-CSRF':csrf}},body:JSON.stringify({{}})}});if(response.status===401||response.status===403)throw new Error('session_expired');result.textContent=response.ok?'Notifica di prova accodata.':'Invio prova non riuscito'}}catch(error){{actionError(error,'Invio prova non riuscito')}}}});</script>
<script>document.querySelectorAll('[data-save]').forEach(button=>button.onclick=async()=>{{const card=button.closest('[data-preferences]'),start=card.querySelector('[data-quiet-start]').value,end=card.querySelector('[data-quiet-end]').value;if((start&&!end)||(!start&&end)){{result.textContent='Compila entrambi gli orari silenziosi oppure lasciali vuoti.';return}}const body={{enabled:card.querySelector('[data-enabled]').checked,categories:[...card.querySelectorAll('[data-category]:checked')].map(x=>x.value),minimum_severity:card.querySelector('[data-severity]').value,vibration:card.querySelector('[data-vibration]').checked,quiet_start:start||null,quiet_end:end||null,timezone:'Europe/Rome'}};try{{const csrf=await csrfToken(),response=await fetch('/bounded/wear/devices/'+button.dataset.save+'/notification-preferences',{{method:'PATCH',headers:{{'Content-Type':'application/json','X-Ekonex-CSRF':csrf}},body:JSON.stringify(body)}});if(response.status===401||response.status===403)throw new Error('session_expired');result.textContent=response.ok?'Preferenze salvate.':'Salvataggio non riuscito'}}catch(error){{actionError(error,'Salvataggio non riuscito')}}}});</script>
<script>document.querySelectorAll('[data-save-quick]').forEach(button=>button.onclick=async()=>{{const card=button.closest('[data-preferences]'),actions=[];for(const row of card.querySelectorAll('[data-quick-slot]')){{const label=row.querySelector('[data-quick-label]').value.trim(),utterance=row.querySelector('[data-quick-utterance]').value.trim();if(!label&&!utterance)continue;if(!label||!utterance){{result.textContent='Ogni comando rapido deve avere sia etichetta sia comando.';return}}actions.push({{slot:Number(row.dataset.quickSlot),label,utterance,icon:row.querySelector('[data-quick-icon]').value,color:row.querySelector('[data-quick-color]').value,confirmation:row.nextElementSibling.querySelector('[data-quick-confirmation]').checked}})}}try{{const csrf=await csrfToken(),response=await fetch('/bounded/wear/devices/'+button.dataset.saveQuick+'/quick-actions',{{method:'PATCH',headers:{{'Content-Type':'application/json','X-Ekonex-CSRF':csrf}},body:JSON.stringify({{actions}})}});if(response.status===401||response.status===403)throw new Error('session_expired');result.textContent=response.ok?'Comandi rapidi salvati. Saranno visibili al prossimo avvio dell app Wear.':'Salvataggio comandi rapidi non riuscito'}}catch(error){{actionError(error,'Salvataggio comandi rapidi non riuscito')}}}});</script>
</body></html>"""
    response = HTMLResponse(page)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    frame_ancestors, _same_site = _frame_policy()
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        f"connect-src 'self'; frame-ancestors {frame_ancestors}"
    )
    return response


@router.delete(
    "/bounded/wear/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_device(
    device_id: str,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    x_ekonex_csrf: Annotated[str | None, Header()] = None,
) -> Response:
    context.require("use")
    if x_ekonex_csrf != _ui_csrf(context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_denied")
    try:
        from uuid import UUID

        parsed = UUID(device_id)
    except ValueError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from error
    device = await session.scalar(
        select(WearDevice).where(
            WearDevice.id == parsed,
            WearDevice.tenant_id == context.tenant_id,
            WearDevice.user_id == context.user_id,
            WearDevice.installation_id == context.effective_installation_id,
            WearDevice.revoked_at.is_(None),
        )
    )
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    device.revoked_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/bounded/wear/devices/{device_id}")
async def update_device_capabilities(
    device_id: UUID,
    payload: DeviceCapabilitiesRequest,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    x_ekonex_csrf: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    context.require("use")
    if x_ekonex_csrf != _ui_csrf(context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_denied")
    if not payload.capabilities or not payload.capabilities <= CAPABILITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_capabilities")
    device = await session.scalar(
        select(WearDevice).where(
            WearDevice.id == device_id,
            WearDevice.tenant_id == context.tenant_id,
            WearDevice.user_id == context.user_id,
            WearDevice.installation_id == context.effective_installation_id,
            WearDevice.revoked_at.is_(None),
        )
    )
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    device.capabilities_json = sorted(payload.capabilities)
    await session.commit()
    return {"device_id": str(device.id), "capabilities": device.capabilities_json}


@router.patch("/bounded/wear/devices/{device_id}/notification-preferences")
async def update_notification_preferences(
    device_id: UUID,
    payload: NotificationPreferencesRequest,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    x_ekonex_csrf: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    context.require("use")
    if x_ekonex_csrf != _ui_csrf(context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_denied")
    if not payload.categories <= set(NOTIFICATION_CATEGORIES):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_categories")
    device = await session.scalar(
        select(WearDevice).where(
            WearDevice.id == device_id,
            WearDevice.tenant_id == context.tenant_id,
            WearDevice.user_id == context.user_id,
            WearDevice.installation_id == context.effective_installation_id,
            WearDevice.revoked_at.is_(None),
        )
    )
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    existing = device.notification_preferences_json or {}
    device.notification_preferences_json = {
        "enabled": payload.enabled,
        "categories": sorted(payload.categories),
        "minimum_severity": payload.minimum_severity,
        "vibration": payload.vibration,
        "quiet_start": payload.quiet_start,
        "quiet_end": payload.quiet_end,
        "timezone": payload.timezone,
        "quick_actions": existing.get("quick_actions", []),
    }
    await session.commit()
    return {"device_id": str(device.id), "preferences": device.notification_preferences_json}


@router.patch("/bounded/wear/devices/{device_id}/quick-actions")
async def update_quick_actions(
    device_id: UUID,
    payload: QuickActionsRequest,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    x_ekonex_csrf: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    context.require("use")
    if x_ekonex_csrf != _ui_csrf(context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_denied")
    device = await session.scalar(
        select(WearDevice).where(
            WearDevice.id == device_id,
            WearDevice.tenant_id == context.tenant_id,
            WearDevice.user_id == context.user_id,
            WearDevice.installation_id == context.effective_installation_id,
            WearDevice.revoked_at.is_(None),
        )
    )
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    preferences = dict(device.notification_preferences_json or {})
    preferences["quick_actions"] = [
        item.model_dump() for item in sorted(payload.actions, key=lambda item: item.slot)
    ]
    device.notification_preferences_json = preferences
    await session.commit()
    return {"device_id": str(device.id), "quick_actions": preferences["quick_actions"]}


@router.post("/bounded/wear/devices/{device_id}/notifications/test")
async def create_test_notification(
    device_id: UUID,
    payload: TestNotificationRequest,
    context: Annotated[BoundContext, bound_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    x_ekonex_csrf: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    context.require("use")
    if x_ekonex_csrf != _ui_csrf(context):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_denied")
    device = await session.scalar(
        select(WearDevice).where(
            WearDevice.id == device_id,
            WearDevice.tenant_id == context.tenant_id,
            WearDevice.user_id == context.user_id,
            WearDevice.installation_id == context.effective_installation_id,
            WearDevice.revoked_at.is_(None),
        )
    )
    if device is None or "notification.receive" not in device.capabilities_json:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    created = await WearNotificationService(session).enqueue_for_installation(
        tenant_id=context.tenant_id,
        installation_id=context.effective_installation_id,
        event_key=f"wear-test:{device.id}:{datetime.now(UTC).isoformat()}",
        category="system",
        severity="info",
        title=payload.title,
        body=payload.body,
    )
    await session.commit()
    return {"created": created}


@router.post("/api/wear/v1/enrollments/claim", response_model=ClaimResponse)
async def claim(
    payload: ClaimRequest,
    request: Request,
    session: Annotated[AsyncSession, session_dependency],
) -> ClaimResponse:
    source = request.headers.get("cf-connecting-ip")
    if not source:
        source = request.client.host if request.client else "unknown"
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    limiter = WearClaimLimiter(session, source_hash)
    failed = await limiter.failed_count()
    if failed >= 5:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "wear_claim_rate_limited")
    try:
        device, credential = await WearDeviceService(session).claim(
            payload.code, payload.display_name
        )
        limiter.record(successful=True)
        await session.commit()
    except WearDenied as error:
        await session.rollback()
        WearClaimLimiter(session, source_hash).record(successful=False)
        await session.commit()
        response_status = (
            status.HTTP_429_TOO_MANY_REQUESTS if failed + 1 >= 5 else status.HTTP_403_FORBIDDEN
        )
        detail = "wear_claim_rate_limited" if response_status == 429 else "wear_claim_denied"
        raise HTTPException(response_status, detail) from error
    return ClaimResponse(device_id=str(device.id), credential=credential)


@router.get("/api/wear/v1/bootstrap")
async def bootstrap(
    context=wear_context_dependency,
    session: Annotated[AsyncSession, session_dependency] = None,
) -> dict[str, object]:
    device = await session.scalar(
        select(WearDevice).where(
            WearDevice.id == context.device_id,
            WearDevice.revoked_at.is_(None),
        )
    )
    preferences = device.notification_preferences_json if device else {}
    return {
        "device_id": str(context.device_id),
        "capabilities": sorted(context.capabilities),
        "quick_actions": (preferences or {}).get("quick_actions", []),
    }


@router.get("/api/wear/v1/notifications")
async def pending_notifications(
    context=wear_context_dependency,
    session: Annotated[AsyncSession, session_dependency] = None,
) -> dict[str, object]:
    items = await WearNotificationService(session).pending(context)
    await session.commit()
    return {
        "notifications": [
            {
                "id": str(item.id),
                "event_key": item.event_key,
                "category": item.category,
                "severity": item.severity,
                "title": item.title,
                "body": item.body,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ]
    }


@router.post(
    "/api/wear/v1/notifications/{notification_id}/ack",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def acknowledge_notification(
    notification_id: UUID,
    context=wear_context_dependency,
    session: Annotated[AsyncSession, session_dependency] = None,
) -> Response:
    try:
        await WearNotificationService(session).acknowledge(context, notification_id)
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except WearDenied as error:
        await session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND) from error


@router.post("/api/wear/v1/ask")
async def ask(
    payload: AskRequest,
    context=wear_context_dependency,
    session: Annotated[AsyncSession, session_dependency] = None,
) -> dict[str, str]:
    context.require("voice.ask")
    installation = await session.scalar(
        select(Installation).where(
            Installation.id == context.installation_id,
            Installation.tenant_id == context.tenant_id,
            Installation.revoked_at.is_(None),
        )
    )
    if installation is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "installation_unavailable")
    last_seen = installation.last_seen_at
    if last_seen is None or (last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=UTC)) < (
        datetime.now(UTC) - timedelta(seconds=LIVENESS_TIMEOUT_SECONDS)
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "installation_offline")
    try:
        command_speech = await _wear_power_command(session, context, payload.utterance)
    except WearDenied as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "command_not_authorized") from error
    if command_speech is not None:
        return {"status": "answered", "speech": command_speech}
    reply = await ConversationEntityService(session).ask_for_scope(
        context.tenant_id,
        context.installation_id,
        payload.utterance,
        named_only=False,
        live_installation_public_id=installation.public_id,
        allow_ai_fallback=False,
    )
    await session.commit()
    return {"status": reply.status.value, "speech": reply.speech}


@router.post("/api/wear/v1/quick-actions/{slot}/execute")
async def execute_quick_action(
    slot: int,
    context=wear_context_dependency,
    session: Annotated[AsyncSession, session_dependency] = None,
) -> dict[str, str]:
    if slot < 1 or slot > 6:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "quick_action_not_found")
    try:
        context.require("command.execute")
    except WearDenied as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "command_not_authorized") from error
    device = await session.scalar(
        select(WearDevice).where(
            WearDevice.id == context.device_id,
            WearDevice.tenant_id == context.tenant_id,
            WearDevice.user_id == context.user_id,
            WearDevice.installation_id == context.installation_id,
            WearDevice.revoked_at.is_(None),
        )
    )
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "quick_action_not_found")
    actions = (device.notification_preferences_json or {}).get("quick_actions", [])
    action = next((item for item in actions if int(item.get("slot", 0)) == slot), None)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "quick_action_not_found")
    return await ask(AskRequest(utterance=str(action["utterance"])), context, session)
