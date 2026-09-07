"""Safe local Alexa announcement service for Ekonex Voice."""

from __future__ import annotations

from collections.abc import Iterable

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_MODE
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import CONF_CLOUD_URL, CONF_INSTALLATION_ID, DOMAIN, LABORATORY_CLOUD_URL
from .models import EkonexVoiceConfigEntry

SERVICE_ANNOUNCE = "announce"
SERVICE_RUN_ROUTINE = "run_voice_routine"
ATTR_TARGETS = "targets"
ATTR_MESSAGE = "message"
MODE_ANNOUNCE = "announce"
MODE_SPEAK = "speak"
MAX_ANNOUNCEMENT_LENGTH = 500
ATTR_ROUTINE = "routine"
ATTR_INSTALLATION_ID = "installation_id"

ANNOUNCE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TARGETS): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(CONF_MODE, default=MODE_ANNOUNCE): vol.In({MODE_ANNOUNCE, MODE_SPEAK}),
    }
)

RUN_ROUTINE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ROUTINE): vol.All(cv.string, vol.Length(min=1, max=64)),
        vol.Optional(ATTR_MESSAGE): vol.All(cv.string, vol.Length(max=500)),
        vol.Optional(ATTR_INSTALLATION_ID): cv.string,
    }
)


def _select_routine_entry(
    entries: Iterable[EkonexVoiceConfigEntry], requested_installation: object | None
) -> EkonexVoiceConfigEntry:
    """Select an explicit installation or the sole laboratory connection."""
    candidates = [
        entry
        for entry in entries
        if entry.state is ConfigEntryState.LOADED
        and (
            not requested_installation
            or str(entry.data.get(CONF_INSTALLATION_ID)) == requested_installation
        )
    ]
    if not requested_installation:
        laboratory_candidates = [
            entry
            for entry in candidates
            if str(entry.data.get(CONF_CLOUD_URL, "")).rstrip("/") == LABORATORY_CLOUD_URL
        ]
        if len(laboratory_candidates) == 1:
            candidates = laboratory_candidates
    if len(candidates) != 1:
        raise vol.Invalid("installation_required_or_unavailable")
    return candidates[0]


def register_announcement_service(hass: HomeAssistant) -> None:
    """Register the integration-wide automation action once."""

    async def handle(call: ServiceCall) -> None:
        await async_announce(
            hass,
            call.data[ATTR_TARGETS],
            call.data[ATTR_MESSAGE],
            call.data[CONF_MODE],
        )

    if not hass.services.has_service(DOMAIN, SERVICE_ANNOUNCE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_ANNOUNCE,
            handle,
            schema=ANNOUNCE_SCHEMA,
            supports_response=SupportsResponse.NONE,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_RUN_ROUTINE):

        async def handle_routine(call: ServiceCall) -> None:
            requested_installation = call.data.get(ATTR_INSTALLATION_ID)
            entry = _select_routine_entry(
                hass.config_entries.async_entries(DOMAIN), requested_installation
            )
            message = call.data.get(ATTR_MESSAGE)
            normalized = " ".join(message.split()) if isinstance(message, str) else None
            if message is not None and (not normalized or "<" in message or ">" in message):
                raise vol.Invalid("invalid_announcement")
            await entry.runtime_data.client.async_trigger_voice_routine(
                call.data[ATTR_ROUTINE], normalized
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_RUN_ROUTINE,
            handle_routine,
            schema=RUN_ROUTINE_SCHEMA,
            supports_response=SupportsResponse.NONE,
        )


async def async_announce(
    hass: HomeAssistant,
    targets: Iterable[str],
    message: str,
    mode: str = MODE_ANNOUNCE,
) -> None:
    """Speak bounded plain text on explicitly exposed Alexa notify entities."""
    normalized = " ".join(message.split())
    if (
        not normalized
        or len(message) > MAX_ANNOUNCEMENT_LENGTH
        or any(ord(character) < 32 and character not in "\n\r\t" for character in message)
        or "<" in message
        or ">" in message
        or mode not in {MODE_ANNOUNCE, MODE_SPEAK}
    ):
        raise vol.Invalid("invalid_announcement")

    registry = er.async_get(hass)
    entries = []
    for entity_id in dict.fromkeys(targets):
        entry = registry.async_get(entity_id)
        if entry is None or not _is_authorized_alexa_target(hass, entry, mode):
            raise vol.Invalid("alexa_target_not_authorized")
        entries.append(entry)

    if not entries:
        raise vol.Invalid("alexa_target_required")
    for entry in entries:
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": entry.entity_id, "message": normalized},
            blocking=True,
        )


def _is_authorized_alexa_target(hass: HomeAssistant, entry: er.RegistryEntry, mode: str) -> bool:
    suffixes = {
        MODE_ANNOUNCE: ("_announce", "_annuncio"),
        MODE_SPEAK: ("_speak", "_parla"),
    }.get(mode, ())
    if not (
        entry.domain == "notify"
        and entry.platform == "alexa_devices"
        and entry.entity_id.endswith(suffixes)
        and not entry.disabled
    ):
        return False
    for config_entry in hass.config_entries.async_entries(DOMAIN):
        typed_entry: EkonexVoiceConfigEntry = config_entry
        if typed_entry.state is not ConfigEntryState.LOADED:
            continue
        inventory = typed_entry.runtime_data.inventory
        if inventory is not None and inventory.is_exposed(entry):
            return True
    return False
