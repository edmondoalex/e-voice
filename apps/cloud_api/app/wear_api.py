# ruff: noqa: E501
"""HTTP contract for official installation-bound Wear devices."""

from __future__ import annotations

import asyncio
import hashlib
import html
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .alexa_device_types import is_gate_override
from .command_dispatch import (
    ActivateCommand,
    CommandDispatchService,
    CoverCommand,
    DomainActionCommand,
    PowerCommand,
    PressCommand,
)
from .conversation_service import ConversationEntityService
from .database import get_database_session
from .domain.models import Entity, Installation, WearDevice, WearEnrollment
from .evcp import LIVENESS_TIMEOUT_SECONDS, sessions
from .installation_bound import BoundContext
from .installation_bound_api import UI_OPERATIONS, _frame_policy, _ui_csrf, bound_context_dependency
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


def _wear_entity_command(entity: Entity, requested: str):
    if requested in {"open", "close"} and is_gate_override(entity):
        operation = "power_on" if requested == "open" else "power_off"
        return PowerCommand(operation=operation), operation
    if requested in {"open", "close", "stop"}:
        return CoverCommand(operation=requested), requested
    return PowerCommand(operation=requested), requested


async def _wear_power_command(session: AsyncSession, context, utterance: str) -> str | None:
    words = _voice_tokens(utterance)
    requested = (
        "power_on"
        if "accendi" in words
        else "power_off"
        if "spegni" in words
        else "open"
        if "apri" in words
        else "close"
        if "chiudi" in words
        else "stop"
        if words & {"ferma", "fermo", "stop"}
        else None
    )
    if requested is None:
        return None
    context.require("command.execute")
    target = words - {"accendi", "spegni", "apri", "chiudi", "ferma", "fermo", "stop", "luce"}
    if not target:
        return "Dimmi quale dispositivo vuoi comandare."
    domains = ("light", "switch") if requested in {"power_on", "power_off"} else ("cover", "switch")
    entities = list(
        (
            await session.scalars(
                select(Entity).where(
                    Entity.installation_id == context.installation_id,
                    Entity.deleted_at.is_(None),
                    Entity.available.is_(True),
                    Entity.ha_domain.in_(domains),
                )
            )
        ).all()
    )
    if requested in {"open", "close", "stop"}:
        entities = [
            entity
            for entity in entities
            if entity.ha_domain == "cover"
            or (requested != "stop" and is_gate_override(entity))
        ]
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
    command, operation = _wear_entity_command(entity, requested)
    outcome = await CommandDispatchService(session, sessions).dispatch(
        context.installation_id, entity.ha_registry_id, command
    )
    await session.commit()
    if outcome.status != "success":
        return "Il comando non è riuscito."
    action = {
        "power_on": "acceso",
        "power_off": "spento",
        "open": "aperto",
        "close": "chiuso",
        "stop": "fermato",
    }[operation]
    if is_gate_override(entity) and operation in {"power_on", "power_off"}:
        action = "aperto" if operation == "power_on" else "chiuso"
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


QUICK_ACTION_STANDARD_ICONS = {
    "light": "Luce",
    "power": "Accensione",
    "climate": "Clima",
    "blind": "Tapparella",
    "lock": "Sicurezza",
    "scene": "Scenario",
    "music": "Musica",
    "mdi:lock": "Serratura",
    "mdi:garage": "Portone",
    "mdi:gate": "Cancello",
    "mdi:sofa": "Relax",
    "mdi:weather-sunset": "Atmosfera serale",
    "mdi:home-export-outline": "Vado via",
    "mdi:home-import-outline": "Arrivo",
    "mdi:star-four-points": "Scenario · Stellina",
    "mdi:toggle-switch": "Scenario ON",
    "mdi:toggle-switch-off": "Scenario OFF",
    "mdi:lightbulb-group": "Luci · Gruppo lampadine",
    "mdi:alarm-light": "Allarme",
    "mdi:shield-alert": "Sicurezza · Allarme",
    "mdi:party-popper": "Festa",
    "mdi:movie-open": "Cinema",
    "mdi:silverware-fork-knife": "Cena",
    "mdi:sleep": "Notte",
    "mdi:sprout": "Giardino",
    "mdi:door": "Porta",
    "mdi:television": "Televisione",
    "mdi:thermostat": "Riscaldamento",
    "mdi:water": "Acqua",
    "mdi:robot-vacuum": "Aspirapolvere",
    "mdi:pool": "Piscina",
    "mdi:cctv": "Telecamera",
    "mdi:power-plug": "Presa",
    "mdi:bed": "Camera",
    "mdi:run": "Movimento",
}


QUICK_ACTION_ICONS = {
    "light",
    "power",
    "climate",
    "blind",
    "lock",
    "scene",
    "music",
    "mdi:lightbulb",
    "mdi:power",
    "mdi:thermostat",
    "mdi:blinds",
    "mdi:lock",
    "mdi:creation",
    "mdi:music",
    "mdi:home",
    "mdi:garage",
    "mdi:fan",
    "mdi:water",
    "mdi:shield-home",
    "mdi:television",
    "mdi:weather-sunny",
    "mdi:bed",
    "mdi:door",
    "mdi:run",
    "mdi:gate",
} | set(QUICK_ACTION_STANDARD_ICONS)
QUICK_ACTION_COLOR_LABELS = {
    "red": "Rosso",
    "blue": "Blu",
    "green": "Verde",
    "orange": "Arancio",
    "purple": "Viola",
    "cyan": "Ciano",
    "yellow": "Giallo",
    "pink": "Rosa",
    "white": "Bianco",
    "gray": "Grigio",
    "teal": "Turchese",
    "lime": "Lime",
    "amber": "Ambra",
}
QUICK_ACTION_COLORS = set(QUICK_ACTION_COLOR_LABELS)
WEAR_TOGGLE_DOMAINS = {"light", "switch", "cover", "lock"}
WEAR_EXTRA_OPERATIONS = {
    "lock": (("lock", "Blocca"), ("unlock", "Sblocca")),
    "button": (("press", "Premi"),),
}


def _wear_operations(domain: str) -> tuple[tuple[str, str], ...]:
    operations = UI_OPERATIONS.get(domain, WEAR_EXTRA_OPERATIONS.get(domain, ()))
    if domain in WEAR_TOGGLE_DOMAINS:
        return (*operations, ("toggle", "Inverti stato"))
    return operations


def _wear_operations_for_entity(entity: Entity) -> tuple[tuple[str, str], ...]:
    if is_gate_override(entity):
        return (
            ("power_on", "Apri"),
            ("power_off", "Chiudi"),
            ("toggle", "Inverti stato"),
        )
    return _wear_operations(entity.ha_domain)


def _wear_success_speech(entity: Entity, operation: str) -> str:
    if is_gate_override(entity):
        return "Ho aperto" if operation == "power_on" else "Ho chiuso"
    return {
        "power_on": "Ho acceso",
        "power_off": "Ho spento",
        "open": "Ho aperto",
        "stop": "Ho fermato",
        "close": "Ho chiuso",
        "activate": "Ho attivato",
        "press": "Ho premuto",
    }[operation]


def _resolve_toggle(domain: str, state_value: str | None) -> str:
    state = (state_value or "").strip().casefold()
    if domain in {"light", "switch"}:
        if state == "on":
            return "power_off"
        if state == "off":
            return "power_on"
    elif domain == "cover":
        if state == "open":
            return "close"
        if state == "closed":
            return "open"
    elif domain == "lock":
        if state == "locked":
            return "unlock"
        if state == "unlocked":
            return "lock"
    raise ValueError("quick_action_state_unknown")


def _state_visual(domain: str, state_value: str | None, icon: str) -> str:
    state = (state_value or "").strip().casefold()
    if domain == "light":
        return "light_on" if state == "on" else "light_off" if state == "off" else "unknown"
    if domain == "cover":
        return (
            "cover_open" if state == "open" else "cover_closed" if state == "closed" else "unknown"
        )
    if domain == "lock":
        return (
            "lock_closed"
            if state == "locked"
            else "lock_open"
            if state == "unlocked"
            else "unknown"
        )
    if domain == "switch" and icon == "lock":
        return "lock_open" if state == "on" else "lock_closed" if state == "off" else "unknown"
    return "on" if state == "on" else "off" if state == "off" else "unknown"


def _is_activating(domain: str, operation: str, state_value: str | None) -> bool:
    if operation == "toggle":
        try:
            operation = _resolve_toggle(domain, state_value)
        except ValueError:
            return False
    return operation in {"power_on", "open", "unlock", "press"}


class QuickActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    slot: int = Field(ge=1, le=32)
    label: str = Field(min_length=1, max_length=24)
    utterance: str | None = Field(default=None, min_length=1, max_length=160)
    entity_id: UUID | None = None
    operation: str | None = Field(
        default=None,
        pattern=r"^(power_on|power_off|toggle|open|stop|close|activate|lock|unlock|press)$",
    )
    icon: str = Field(default="scene")
    color: str = Field(default="blue")
    border_color: str = Field(default="white")
    confirmation: bool = False
    confirmation_on_activate: bool = False

    @model_validator(mode="after")
    def valid_target(self):
        direct = self.entity_id is not None or self.operation is not None
        if direct and (self.entity_id is None or self.operation is None):
            raise ValueError("incomplete_quick_action_target")
        if not direct and not self.utterance:
            raise ValueError("missing_quick_action_target")
        return self

    @field_validator("icon")
    @classmethod
    def valid_icon(cls, value: str) -> str:
        if value not in QUICK_ACTION_ICONS and not re.fullmatch(
            r"mdi:[a-z0-9][a-z0-9_-]{0,79}", value
        ):
            raise ValueError("invalid_quick_action_icon")
        return value

    @field_validator("color", "border_color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        if value not in QUICK_ACTION_COLORS:
            raise ValueError("invalid_quick_action_color")
        return value


class QuickActionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[QuickActionRequest] = Field(max_length=32)

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
    command_entities = (
        await session.scalars(
            select(Entity)
            .where(
                Entity.installation_id == context.effective_installation_id,
                Entity.deleted_at.is_(None),
                Entity.available.is_(True),
                Entity.ha_registry_id.is_not(None),
                Entity.ha_domain.in_(tuple(set(UI_OPERATIONS) | set(WEAR_EXTRA_OPERATIONS))),
            )
            .order_by(
                Entity.ha_domain, Entity.voice_name, Entity.display_name, Entity.friendly_name
            )
        )
    ).all()
    command_entities = sorted(
        command_entities,
        key=lambda entity: (
            str(
                entity.voice_name
                or entity.display_name
                or entity.friendly_name
                or entity.ha_entity_id
                or ""
            ).casefold(),
            str(entity.ha_domain or "").casefold(),
        ),
    )

    domain_labels = {
        "light": "Luce",
        "switch": "Interruttore",
        "cover": "Tapparella/tenda",
        "scene": "Scenario",
        "script": "Script",
        "lock": "Serratura/portone",
        "button": "Pulsante",
    }
    domain_icons = {
        "light": "light",
        "switch": "power",
        "cover": "blind",
        "scene": "scene",
        "script": "scene",
        "lock": "mdi:lock",
        "button": "mdi:gesture-tap-button",
    }
    spoken_operations = {
        "power_on": "Accendi",
        "power_off": "Spegni",
        "toggle": "Inverti stato",
        "open": "Apri",
        "stop": "Stop",
        "close": "Chiudi",
        "activate": "Attiva",
        "lock": "Blocca",
        "unlock": "Sblocca",
        "press": "Premi",
    }

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
        icon_labels = QUICK_ACTION_STANDARD_ICONS
        action_rows = []
        visible_slots = max(8, max(saved_actions, default=0))
        for slot in range(1, visible_slots + 1):
            action = saved_actions.get(slot, {})
            icon = str(action.get("icon") or "scene")
            standard_icon = icon if icon in icon_labels else "__custom__"
            icon_options = "".join(
                f'<option value="{value}" {"selected" if value == standard_icon else ""}>{label}</option>'
                for value, label in icon_labels.items()
            )
            icon_options += f'<option value="__custom__" {"selected" if standard_icon == "__custom__" else ""}>Custom MDI</option>'
            color = str(action.get("color") or "blue")
            color_options = "".join(
                f'<option value="{value}" {"selected" if value == color else ""}>{label}</option>'
                for value, label in QUICK_ACTION_COLOR_LABELS.items()
            )
            border_color = str(action.get("border_color") or "white")
            border_color_options = "".join(
                f'<option value="{value}" {"selected" if value == border_color else ""}>{label}</option>'
                for value, label in QUICK_ACTION_COLOR_LABELS.items()
            )
            selected_target = (
                f"{action.get('entity_id')}|{action.get('operation')}"
                if action.get("entity_id") and action.get("operation")
                else ""
            )
            command_options = ['<option value="">Scegli dispositivo e azione</option>']
            for entity in command_entities:
                name = (
                    entity.voice_name
                    or entity.display_name
                    or entity.friendly_name
                    or entity.ha_entity_id
                )
                for operation, operation_label in _wear_operations_for_entity(entity):
                    target = f"{entity.id}|{operation}"
                    utterance = f"{spoken_operations[operation]} {name}"
                    command_options.append(
                        f'<option value="{target}" data-label="{html.escape(operation_label + " " + name, quote=True)}" '
                        f'data-icon="{domain_icons[entity.ha_domain]}" data-utterance="{html.escape(utterance, quote=True)}" '
                        f"{'selected' if target == selected_target else ''}>"
                        f"{html.escape(domain_labels[entity.ha_domain])} — {html.escape(name)} — {html.escape(operation_label)}</option>"
                    )
            legacy_utterance = str(action.get("utterance") or "")
            if legacy_utterance and not selected_target:
                command_options.append(
                    f'<option value="" selected>Comando precedente — {html.escape(legacy_utterance)}</option>'
                )
            action_rows.append(
                f'<div class=quick-item draggable=true><div class=quick-row data-quick-slot="{slot}"><div class=quick-order title="Trascina per riordinare">'
                f'<svg class=quick-handle viewBox="0 0 24 24" aria-hidden=true><path fill="currentColor" d="M7 5h2v2H7V5zm8 0h2v2h-2V5zM7 11h2v2H7v-2zm8 0h2v2h-2v-2zM7 17h2v2H7v-2zm8 0h2v2h-2v-2z"></path></svg>'
                f'<span data-quick-position>{slot}</span></div>'
                f'<input data-quick-label maxlength=24 placeholder="Etichetta" value="{html.escape(str(action.get("label") or ""))}">'
                f'<input data-quick-search type=search placeholder="Cerca dispositivo o azione" autocomplete=off>'
                f"<select data-quick-command>{''.join(command_options)}</select>"
                f'<input type=hidden data-quick-utterance value="{html.escape(legacy_utterance, quote=True)}">'
                f"<select data-quick-icon-standard>{icon_options}</select>"
                f'<input data-quick-icon-custom placeholder="mdi:nome-icona" value="{html.escape(icon if standard_icon == "__custom__" else "", quote=True)}" {"" if standard_icon == "__custom__" else "hidden"}>'
                f"<select data-quick-color title=\"Colore tasto\">{color_options}</select>"
                f"<select data-quick-border-color title=\"Colore bordo\">{border_color_options}</select></div>"
                f"<div class=quick-confirm><label><input type=checkbox data-quick-confirmation {'checked' if action.get('confirmation', False) else ''}> Conferma sempre</label>"
                f"<label><input type=checkbox data-quick-confirmation-on-activate {'checked' if action.get('confirmation_on_activate', False) else ''}> Conferma solo per accensione/apertura</label></div></div>"
            )
        quick_actions = "".join(action_rows)
        return f'''<article class=device-card data-preferences="{item.id}"><h2>{html.escape(item.display_name)}</h2>
<p class=muted>{html.escape(", ".join(item.capabilities_json))}</p>
<details class=quick-actions open><summary>Comandi rapidi</summary><p class=muted>I primi otto tasti sono sempre disponibili. Usa + per aggiungerne altri; dal nono in poi compaiono nell area launcher inferiore. Scegli un'icona standard oppure Custom MDI e inserisci, per esempio, mdi:gate.</p><div data-quick-rows>{quick_actions}</div><button type=button data-add-quick>+ Aggiungi tasto</button> <button data-save-quick="{item.id}">Salva comandi rapidi</button></details>
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
<style>:root{{--page:#384044;--panel:#1f1f1f;--panel-soft:#25292b;--field:#111;--border:#55d6e8;--line:#465158;--accent:#49d3f2;--active:#3d8ca2;--text:#f7f7f7;--muted:#b9c9d1;--danger:#ef3027}}*{{box-sizing:border-box}}body{{font:16px system-ui;background:var(--page);color:var(--text);margin:0;padding:28px}}button,select,input{{padding:11px 14px;border:1px solid var(--border);border-radius:11px;background:var(--field);color:var(--text)}}button{{font-weight:700;cursor:pointer}}button:hover{{background:var(--active)}}code{{font-size:18px;color:var(--accent)}}h1{{margin-top:0}}.device-card,.installer-guide{{width:100%;margin:18px 0;padding:24px;border:1px solid var(--line);border-radius:18px;background:var(--panel)}}.device-card h2{{margin-top:0}}.installer-guide summary,.quick-actions summary{{cursor:pointer;font-size:20px;font-weight:750}}.installer-guide ol{{line-height:1.55}}.installer-guide .warning{{padding:12px;border-left:4px solid #f4b942;background:var(--panel-soft)}}.badge{{display:inline-block;margin-left:8px;padding:3px 8px;border-radius:999px;background:var(--active);color:#fff;font-size:12px;vertical-align:middle}}.muted{{color:var(--muted)}}fieldset,.quick-actions{{margin:16px 0;border:1px solid var(--line);border-radius:14px;padding:16px;background:var(--panel)}}.categories,.actions,.quiet{{display:flex;gap:14px;flex-wrap:wrap;align-items:center}}.categories label,.switch{{display:flex;gap:8px;align-items:center;padding:8px}}.actions{{margin-top:18px}}.quick-item{{border-radius:12px;transition:background .15s,opacity .15s}}.quick-item.dragging{{opacity:.55;background:#31434a}}.quick-row{{display:grid;grid-template-columns:96px minmax(150px,1fr) minmax(210px,1.4fr) minmax(320px,2.2fr) 130px minmax(170px,1fr) 120px 120px;gap:9px;align-items:center;margin:10px 0}}.quick-order{{height:44px;display:flex;align-items:center;justify-content:center;gap:8px;border:1px solid var(--border);border-radius:11px;background:var(--field);cursor:grab;user-select:none}}.quick-order:active{{cursor:grabbing}}.quick-handle{{width:22px;height:22px;color:var(--muted);flex:none}}.quick-confirm{{display:flex;align-items:center;gap:8px;margin:0 0 12px 38px}}.danger{{border-color:var(--danger);color:#ffb4ac}}.danger:hover{{background:#6f1713}}@media(max-width:980px){{body{{padding:14px}}.quick-row{{grid-template-columns:52px 1fr}}.quick-row input,.quick-row select{{grid-column:2}}.quick-confirm{{margin-left:38px}}}}</style></head><body>
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
<script>
function setQuickIcon(row,value){{const standard=row.querySelector('[data-quick-icon-standard]'),custom=row.querySelector('[data-quick-icon-custom]'),known=[...standard.options].some(option=>option.value===value&&value!=='__custom__');standard.value=known?value:'__custom__';custom.hidden=known;if(!known)custom.value=value||''}}
function initQuickRow(row){{const select=row.querySelector('[data-quick-command]'),search=row.querySelector('[data-quick-search]'),standard=row.querySelector('[data-quick-icon-standard]'),custom=row.querySelector('[data-quick-icon-custom]'),allOptions=[...select.options].map(option=>option.cloneNode(true));standard.onchange=()=>{{custom.hidden=standard.value!=='__custom__';if(!custom.hidden)custom.focus()}};select.onchange=()=>{{const option=select.selectedOptions[0],label=row.querySelector('[data-quick-label]'),utterance=row.querySelector('[data-quick-utterance]');if(!select.value)return;if(!label.value.trim())label.value=(option.dataset.label||'').slice(0,24);if(option.dataset.icon)setQuickIcon(row,option.dataset.icon);utterance.value=option.dataset.utterance||''}};search.oninput=()=>{{const query=search.value.trim().toLocaleLowerCase('it'),current=select.value;select.replaceChildren(...allOptions.filter((option,index)=>index===0||!query||option.textContent.toLocaleLowerCase('it').includes(query)).map(option=>option.cloneNode(true)));if([...select.options].some(option=>option.value===current))select.value=current;else select.selectedIndex=0}}}}
function renumberQuickRows(rows){{[...rows.querySelectorAll('.quick-item')].forEach((item,index)=>{{const row=item.querySelector('[data-quick-slot]'),slot=index+1;row.dataset.quickSlot=slot;row.querySelector('[data-quick-position]').textContent=slot}})}}
let draggedQuickItem=null;
function initQuickItem(item){{item.ondragstart=event=>{{draggedQuickItem=item;item.classList.add('dragging');event.dataTransfer.effectAllowed='move'}};item.ondragend=()=>{{item.classList.remove('dragging');draggedQuickItem=null}};item.ondragover=event=>{{event.preventDefault();event.dataTransfer.dropEffect='move'}};item.ondrop=event=>{{event.preventDefault();if(!draggedQuickItem||draggedQuickItem===item)return;const rows=item.parentElement,items=[...rows.querySelectorAll('.quick-item')],from=items.indexOf(draggedQuickItem),to=items.indexOf(item);rows.insertBefore(draggedQuickItem,from<to?item.nextSibling:item);renumberQuickRows(rows)}}}}
document.querySelectorAll('[data-quick-slot]').forEach(initQuickRow);
document.querySelectorAll('.quick-item').forEach(initQuickItem);
</script>
<script>document.querySelectorAll('[data-add-quick]').forEach(button=>button.onclick=()=>{{const rows=button.closest('.quick-actions').querySelector('[data-quick-rows]'),existing=[...rows.querySelectorAll('.quick-item')];if(existing.length>=32)return;const item=existing[existing.length-1].cloneNode(true),row=item.querySelector('[data-quick-slot]');row.querySelectorAll('input').forEach(input=>{{if(input.type==='checkbox')input.checked=false;else input.value=''}});row.querySelectorAll('select').forEach(select=>select.selectedIndex=0);rows.append(item);renumberQuickRows(rows);setQuickIcon(row,'scene');initQuickRow(row);initQuickItem(item)}});</script>
<script>document.querySelectorAll('[data-save-quick]').forEach(button=>button.onclick=async()=>{{const card=button.closest('[data-preferences]'),actions=[];for(const row of card.querySelectorAll('[data-quick-slot]')){{const label=row.querySelector('[data-quick-label]').value.trim(),utterance=row.querySelector('[data-quick-utterance]').value.trim(),target=row.querySelector('[data-quick-command]').value;if(!label&&!target&&!utterance)continue;if(!label||(!target&&!utterance)){{result.textContent='Scegli dispositivo e azione e inserisci l etichetta del tasto.';return}}const standard=row.querySelector('[data-quick-icon-standard]').value,custom=row.querySelector('[data-quick-icon-custom]').value.trim(),icon=standard==='__custom__'?custom:standard;if(!icon){{result.textContent='Inserisci il nome dell icona Custom MDI.';return}}const parts=target?target.split('|'):[null,null],confirmations=row.closest('.quick-item').querySelector('.quick-confirm');actions.push({{slot:Number(row.querySelector('[data-quick-position]').textContent),label,utterance:utterance||null,entity_id:parts[0],operation:parts[1],icon,color:row.querySelector('[data-quick-color]').value,border_color:row.querySelector('[data-quick-border-color]').value,confirmation:confirmations.querySelector('[data-quick-confirmation]').checked,confirmation_on_activate:confirmations.querySelector('[data-quick-confirmation-on-activate]').checked}})}}try{{const csrf=await csrfToken(),response=await fetch('/bounded/wear/devices/'+button.dataset.saveQuick+'/quick-actions',{{method:'PATCH',headers:{{'Content-Type':'application/json','X-Ekonex-CSRF':csrf}},body:JSON.stringify({{actions}})}});if(response.status===401||response.status===403)throw new Error('session_expired');result.textContent=response.ok?'Comandi rapidi salvati. Saranno visibili sul Wear entro 15 secondi.':'Salvataggio comandi rapidi non riuscito'}}catch(error){{actionError(error,'Salvataggio comandi rapidi non riuscito')}}}});</script>
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
    direct_actions = [item for item in payload.actions if item.entity_id is not None]
    if direct_actions:
        entity_ids = {item.entity_id for item in direct_actions}
        entities = {
            item.id: item
            for item in (
                await session.scalars(
                    select(Entity).where(
                        Entity.id.in_(entity_ids),
                        Entity.installation_id == context.effective_installation_id,
                        Entity.deleted_at.is_(None),
                        Entity.available.is_(True),
                        Entity.ha_registry_id.is_not(None),
                    )
                )
            ).all()
        }
        for action in direct_actions:
            entity = entities.get(action.entity_id)
            if entity is None or action.operation not in {
                operation for operation, _label in _wear_operations(entity.ha_domain)
            }:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, "quick_action_target_invalid"
                )
    preferences = dict(device.notification_preferences_json or {})
    preferences["quick_actions"] = [
        item.model_dump(mode="json") for item in sorted(payload.actions, key=lambda item: item.slot)
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
    configured_actions = list((preferences or {}).get("quick_actions", []))
    entity_ids: set[UUID] = set()
    for action in configured_actions:
        try:
            if action.get("entity_id"):
                entity_ids.add(UUID(str(action["entity_id"])))
        except (TypeError, ValueError):
            continue
    entities = {
        entity.id: entity
        for entity in (
            await session.scalars(
                select(Entity).where(
                    Entity.id.in_(entity_ids),
                    Entity.installation_id == context.installation_id,
                    Entity.deleted_at.is_(None),
                )
            )
        ).all()
    }
    quick_actions = []
    for configured in configured_actions:
        action = dict(configured)
        try:
            entity = entities.get(UUID(str(action.get("entity_id"))))
        except (TypeError, ValueError):
            entity = None
        if entity is not None:
            action["entity_domain"] = entity.ha_domain
            action["entity_state"] = entity.state if entity.available else "unavailable"
            action["state_visual"] = _state_visual(
                entity.ha_domain,
                entity.state if entity.available else None,
                str(action.get("icon") or ""),
            )
            action["activating_action"] = _is_activating(
                entity.ha_domain,
                str(action.get("operation") or ""),
                entity.state if entity.available else None,
            )
        quick_actions.append(action)
    return {
        "device_id": str(context.device_id),
        "capabilities": sorted(context.capabilities),
        "quick_actions": quick_actions,
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


@router.get("/api/wear/v1/notifications/wait")
async def wait_for_notifications(
    context=wear_context_dependency,
    session: Annotated[AsyncSession, session_dependency] = None,
) -> dict[str, object]:
    """Hold one efficient request until a notification is ready or the proxy-safe timeout expires."""
    deadline = datetime.now(UTC) + timedelta(seconds=45)
    while True:
        items = await WearNotificationService(session).pending(context)
        if items or datetime.now(UTC) >= deadline:
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
        await asyncio.sleep(3)


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
    if slot < 1 or slot > 32:
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
    if action.get("entity_id") and action.get("operation"):
        try:
            entity_id = UUID(str(action["entity_id"]))
        except ValueError:
            raise HTTPException(status.HTTP_409_CONFLICT, "quick_action_target_invalid") from None
        entity = await session.scalar(
            select(Entity).where(
                Entity.id == entity_id,
                Entity.installation_id == context.installation_id,
                Entity.deleted_at.is_(None),
                Entity.available.is_(True),
            )
        )
        operation = str(action["operation"])
        if (
            entity is None
            or not entity.ha_registry_id
            or operation not in {item[0] for item in _wear_operations_for_entity(entity)}
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "quick_action_target_unavailable")
        if operation == "toggle":
            try:
                operation = _resolve_toggle(entity.ha_domain, entity.state)
            except ValueError:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "quick_action_state_unknown"
                ) from None
        if operation in {"power_on", "power_off"}:
            command = PowerCommand(operation=operation)
        elif operation in {"open", "stop", "close"}:
            command = CoverCommand(operation=operation)
        elif operation == "activate":
            command = ActivateCommand(operation="activate")
        elif operation == "press":
            command = PressCommand(operation="press")
        elif operation in {"lock", "unlock"}:
            command = DomainActionCommand(operation=operation)
        else:
            raise HTTPException(status.HTTP_409_CONFLICT, "quick_action_target_invalid")
        outcome = await CommandDispatchService(session, sessions).dispatch(
            context.installation_id, entity.ha_registry_id, command
        )
        await session.commit()
        if outcome.status != "success":
            return {"status": "failed", "speech": "Il comando non è riuscito."}
        name = entity.voice_name or entity.display_name or entity.friendly_name or "dispositivo"
        spoken = _wear_success_speech(entity, operation)
        return {"status": "answered", "speech": f"{spoken} {name}."}
    utterance = str(action.get("utterance") or "").strip()
    if not utterance:
        raise HTTPException(status.HTTP_409_CONFLICT, "quick_action_target_invalid")
    return await ask(AskRequest(utterance=utterance), context, session)
