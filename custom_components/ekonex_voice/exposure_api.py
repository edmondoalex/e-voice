"""Administrative Home Assistant WebSocket API for the single eVoice exposure set."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_EXPOSED_DEVICE_IDS,
    CONF_EXPOSED_ENTITY_REGISTRY_IDS,
    CONF_EXPOSURE_LABEL_ID,
    CONF_MEDIA_EXPERIENCES,
    DOMAIN,
)

GET_COMMAND = "ekonex_voice/exposure/get"
SET_COMMAND = "ekonex_voice/exposure/set"
MAX_EXPOSED_ENTITIES = 10_000


def _entry(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry:
    entries = hass.config_entries.async_entries(DOMAIN)
    if entry_id:
        matches = [item for item in entries if item.entry_id == entry_id]
    else:
        matches = entries
        if len(matches) > 1:
            media_entries = [item for item in matches if CONF_MEDIA_EXPERIENCES in item.options]
            if len(media_entries) == 1:
                matches = media_entries
    if len(matches) != 1:
        raise ValueError("entry_not_unique")
    return matches[0]


def _selected_registry_ids(hass: HomeAssistant, entry: Any) -> set[str]:
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    labels = entry.options.get(CONF_EXPOSURE_LABEL_ID)
    selected_entities = set(entry.options.get(CONF_EXPOSED_ENTITY_REGISTRY_IDS, []))
    selected_devices = set(entry.options.get(CONF_EXPOSED_DEVICE_IDS, []))
    result: set[str] = set()
    for entity in entities.entities.values():
        device = devices.async_get(entity.device_id) if entity.device_id else None
        if (
            entity.id in selected_entities
            or entity.device_id in selected_devices
            or (
                labels is not None
                and (labels in entity.labels or (device is not None and labels in device.labels))
            )
        ):
            result.add(entity.id)
    return result


def _catalog(hass: HomeAssistant, entry: Any) -> dict[str, object]:
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    areas = ar.async_get(hass)
    selected = _selected_registry_ids(hass, entry)
    values: list[dict[str, object]] = []
    for entity in sorted(entities.entities.values(), key=lambda item: item.entity_id):
        if entity.disabled:
            continue
        state = hass.states.get(entity.entity_id)
        device = devices.async_get(entity.device_id) if entity.device_id else None
        area_id = entity.area_id or (device.area_id if device is not None else None)
        area = areas.async_get_area(area_id) if area_id else None
        values.append(
            {
                "registry_id": entity.id,
                "entity_id": entity.entity_id,
                "domain": entity.domain,
                "name": entity.name
                or entity.original_name
                or (state.name if state is not None else entity.entity_id),
                "area_id": area_id,
                "area_name": area.name if area is not None else None,
                "device_id": entity.device_id,
                "selected": entity.id in selected,
                "available": state is not None and state.state not in {"unavailable", "unknown"},
            }
        )
    return {
        "contract_version": "ekonex.voice-exposure.v1",
        "entry_id": entry.entry_id,
        "source": "home_assistant",
        "configuration_owner": "e-control",
        "selected_count": len(selected),
        "entities": values,
    }


def _updated_options(entry: Any, requested: list[str]) -> dict[str, object]:
    """Collapse legacy device/label selectors into one explicit registry selection."""
    media = entry.options.get(CONF_MEDIA_EXPERIENCES, {})
    retained_media = (
        {
            identity: values
            for identity, values in media.items()
            if identity in requested and isinstance(values, list)
        }
        if isinstance(media, dict)
        else {}
    )
    options = dict(entry.options)
    options[CONF_EXPOSED_ENTITY_REGISTRY_IDS] = sorted(requested)
    options[CONF_EXPOSED_DEVICE_IDS] = []
    options.pop(CONF_EXPOSURE_LABEL_ID, None)
    options[CONF_MEDIA_EXPERIENCES] = retained_media
    return options


@websocket_command({vol.Required("type"): GET_COMMAND, vol.Optional("entry_id"): str})
@require_admin
@async_response
async def websocket_get_exposure(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the complete HA registry plus the effective current selection."""
    try:
        entry = _entry(hass, msg.get("entry_id"))
    except ValueError as error:
        connection.send_error(msg["id"], "entry_not_unique", str(error))
        return
    connection.send_result(msg["id"], _catalog(hass, entry))


@websocket_command(
    {
        vol.Required("type"): SET_COMMAND,
        vol.Optional("entry_id"): str,
        vol.Required("registry_ids"): [str],
    }
)
@require_admin
@async_response
async def websocket_set_exposure(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Replace the selection atomically and preserve compatible media metadata."""
    requested = msg["registry_ids"]
    if len(requested) > MAX_EXPOSED_ENTITIES or len(requested) != len(set(requested)):
        connection.send_error(msg["id"], "selection_invalid", "invalid exposure selection")
        return
    try:
        entry = _entry(hass, msg.get("entry_id"))
    except ValueError as error:
        connection.send_error(msg["id"], "entry_not_unique", str(error))
        return
    registry = er.async_get(hass)
    if any(registry.entities.get_entry(identity) is None for identity in requested):
        connection.send_error(msg["id"], "entity_not_found", "unknown registry identity")
        return
    options = _updated_options(entry, requested)
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.config_entries.async_reload(entry.entry_id)
    connection.send_result(
        msg["id"],
        {
            "contract_version": "ekonex.voice-exposure.v1",
            "entry_id": entry.entry_id,
            "selected_count": len(requested),
            "updated": True,
        },
    )


def register_exposure_api(hass: HomeAssistant) -> None:
    """Register the admin-only API once during integration setup."""
    websocket_api.async_register_command(hass, websocket_get_exposure)
    websocket_api.async_register_command(hass, websocket_set_exposure)
