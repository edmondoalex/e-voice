"""Native Home Assistant entity inventory and state synchronization."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from aiohttp import ClientWebSocketResponse
from homeassistant.const import (
    ATTR_FRIENDLY_NAME,
    EVENT_STATE_CHANGED,
    MATCH_ALL,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.event import (
    async_track_device_registry_updated_event,
    async_track_entity_registry_updated_event,
)

from .evcp import MAX_MESSAGE_BYTES, envelope

ATTRIBUTE_ALLOWLIST = {
    "light": frozenset(
        {
            "brightness",
            "color_mode",
            "color_temp_kelvin",
            "rgb_color",
            "hs_color",
            "xy_color",
            "effect",
        }
    ),
    "cover": frozenset({"current_position"}),
    "climate": frozenset({"temperature", "hvac_modes", "min_temp", "max_temp"}),
    "fan": frozenset({"percentage"}),
}
CHUNK_TARGET_BYTES = 48_000
COALESCE_SECONDS = 0.25
_LOGGER = logging.getLogger(__name__)


class EntityInventorySynchronizer:
    """Send deterministic snapshots and coalesced deltas for one EVCP session."""

    def __init__(
        self,
        hass: HomeAssistant,
        device_ids: set[str],
        registry_ids: set[str],
        label_id: str | None,
    ) -> None:
        self._hass = hass
        self._device_ids, self._registry_ids = device_ids, registry_ids
        self._label_id = label_id
        self._websocket: ClientWebSocketResponse | None = None
        self._session_id: str | None = None
        self._revision = 0
        self._unsubscribers: list[Callable[[], None]] = []
        self._pending: set[str] = set()
        self._flush_task: asyncio.Task[None] | None = None
        self._resync_task: asyncio.Task[None] | None = None
        self.last_full_revision: int | None = None
        self.last_full_entity_count: int | None = None
        self.last_state_entity_count: int | None = None
        self.send_failure_count = 0
        self.last_error_code: str | None = None

    @property
    def exposure_summary(self) -> dict[str, object]:
        return {
            "ui_device_count": len(self._device_ids),
            "ui_entity_count": len(self._registry_ids),
            "label_configured": self._label_id is not None,
            "label_id": self._label_id,
            "last_full_revision": self.last_full_revision,
            "last_full_entity_count": self.last_full_entity_count,
            "last_state_entity_count": self.last_state_entity_count,
            "send_failure_count": self.send_failure_count,
            "last_error_code": self.last_error_code,
        }

    def is_exposed(self, entry: er.RegistryEntry) -> bool:
        """Return whether the installer currently authorizes this registry entity."""
        device = dr.async_get(self._hass).async_get(entry.device_id) if entry.device_id else None
        return (
            entry.id in self._registry_ids
            or entry.device_id in self._device_ids
            or (
                self._label_id is not None
                and (
                    self._label_id in entry.labels
                    or (device is not None and self._label_id in device.labels)
                )
            )
        )

    async def async_start(
        self, websocket: ClientWebSocketResponse, session_id: str, cloud_revision: int
    ) -> None:
        await self.async_stop()
        self._websocket, self._session_id, self._revision = websocket, session_id, cloud_revision
        _LOGGER.info("Starting entity synchronization from cloud revision %d", cloud_revision)
        self._unsubscribers.append(
            self._hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_changed)
        )
        self._unsubscribers.append(
            async_track_device_registry_updated_event(self._hass, MATCH_ALL, self._registry_changed)
        )
        self._unsubscribers.append(
            self._hass.bus.async_listen(lr.EVENT_LABEL_REGISTRY_UPDATED, self._registry_changed)
        )
        self._unsubscribers.append(
            async_track_entity_registry_updated_event(self._hass, MATCH_ALL, self._registry_changed)
        )
        await self._send_full()

    async def async_stop(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        for task in (self._flush_task, self._resync_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._flush_task = self._resync_task = None
        self._pending.clear()
        self._websocket = None

    @callback
    def _state_changed(self, event: Event[Any]) -> None:
        entity_id = event.data.get("entity_id")
        if isinstance(entity_id, str):
            self._pending.add(entity_id)
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = self._hass.async_create_background_task(
                    self._flush_states(), "ekonex_voice_state_coalesce", eager_start=True
                )

    @callback
    def _registry_changed(self, event: Event[Any]) -> None:
        if self._resync_task is None or self._resync_task.done():
            self._resync_task = self._hass.async_create_background_task(
                self._delayed_full(), "ekonex_voice_inventory_resync", eager_start=True
            )

    async def _delayed_full(self) -> None:
        await asyncio.sleep(COALESCE_SECONDS)
        await self._send_full()

    async def _flush_states(self) -> None:
        await asyncio.sleep(COALESCE_SECONDS)
        entity_ids, self._pending = sorted(self._pending), set()
        registry = er.async_get(self._hass)
        items = [
            item
            for entity_id in entity_ids
            if (item := self._serialize(registry.async_get(entity_id))) is not None
        ]
        if items:
            await self._send("state_update", items)

    async def _send_full(self) -> None:
        registry = er.async_get(self._hass)
        items = [
            item
            for entry in sorted(registry.entities.values(), key=lambda value: value.id)
            if (item := self._serialize(entry)) is not None
        ]
        await self._send("inventory_full", items)

    async def _send(self, message_type: str, items: list[dict[str, object]]) -> None:
        if self._websocket is None or self._session_id is None:
            return
        self._revision += 1
        chunks = _chunks(items)
        try:
            for index, chunk in enumerate(chunks):
                message = envelope(
                    message_type,
                    {
                        "session_id": self._session_id,
                        "revision": self._revision,
                        "batch_index": index,
                        "batch_count": len(chunks),
                        "entities": chunk,
                    },
                )
                if len(json.dumps(message, separators=(",", ":")).encode()) > MAX_MESSAGE_BYTES:
                    raise ValueError("inventory_message_too_large")
                await self._websocket.send_json(message)
        except Exception:
            self.send_failure_count += 1
            self.last_error_code = "send_failed"
            _LOGGER.warning(
                "Entity synchronization send failed: type=%s revision=%d batches=%d",
                message_type,
                self._revision,
                len(chunks),
            )
            raise
        self.last_error_code = None
        if message_type == "inventory_full":
            self.last_full_revision = self._revision
            self.last_full_entity_count = len(items)
            _LOGGER.info(
                "Entity inventory snapshot sent: revision=%d entities=%d batches=%d",
                self._revision,
                len(items),
                len(chunks),
            )
        elif message_type == "state_update":
            self.last_state_entity_count = len(items)
            _LOGGER.debug(
                "Entity state update sent: revision=%d entities=%d",
                self._revision,
                len(items),
            )

    def _serialize(self, entry: er.RegistryEntry | None) -> dict[str, object] | None:
        if entry is None:
            return None
        if not self.is_exposed(entry):
            return None
        return _serialize(self._hass, entry)


def _serialize(hass: HomeAssistant, entry: er.RegistryEntry | None) -> dict[str, object] | None:
    if entry is None or entry.disabled:
        return None
    state = hass.states.get(entry.entity_id)
    if state is None:
        return None
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    area_id = entry.area_id or (device.area_id if device else None)
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return {
        "registry_id": entry.id,
        "entity_id": entry.entity_id,
        "domain": entry.domain,
        "friendly_name": _bound(_friendly_name(entry, state)),
        "area_id": area_id,
        "area_name": _bound(area.name if area else None),
        "device_id": entry.device_id,
        "device_name": _bound(device.name_by_user or device.name if device else None),
        "device_class": _bound(entry.device_class or state.attributes.get("device_class")),
        "supported_features": int(state.attributes.get("supported_features", 0) or 0),
        "state": None if state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE} else _bound(state.state),
        "available": state.state != STATE_UNAVAILABLE,
        "attributes": _attributes(entry.domain, state),
        "last_changed_at": state.last_changed.isoformat().replace("+00:00", "Z"),
        "removed": False,
    }


def _friendly_name(entry: er.RegistryEntry, state: State) -> object | None:
    """Resolve the current HA-visible name while preserving user overrides."""
    name_by_user: object | None = getattr(entry, "name_by_user", None)
    if name_by_user:
        return name_by_user
    if entry.name and entry.name != entry.original_name:
        return entry.name
    return (
        state.attributes.get(ATTR_FRIENDLY_NAME) or entry.name or entry.original_name or state.name
    )


def _attributes(domain: str, state: State) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in ATTRIBUTE_ALLOWLIST.get(domain, frozenset()):
        if key not in state.attributes:
            continue
        value = state.attributes.get(key)
        if isinstance(value, str):
            result[key] = value[:255]
        elif isinstance(value, (int, float, bool)) or value is None:
            result[key] = value
        elif (
            isinstance(value, (list, tuple))
            and len(value) <= 8
            and all(isinstance(item, (str, int, float, bool)) for item in value)
        ):
            result[key] = [item[:255] if isinstance(item, str) else item for item in value]
    return result


def _bound(value: object | None) -> str | None:
    return str(value)[:255] if value is not None else None


def _chunks(items: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    if not items:
        return [[]]
    chunks: list[list[dict[str, object]]] = [[]]
    for item in items:
        candidate = [*chunks[-1], item]
        if (
            chunks[-1]
            and len(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode())
            > CHUNK_TARGET_BYTES
        ):
            chunks.append([item])
        else:
            chunks[-1] = candidate
    return chunks
