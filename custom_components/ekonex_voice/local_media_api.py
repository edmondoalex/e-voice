"""Authenticated local Media API for the e-Face X4 Home Assistant add-on."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterable
from contextlib import suppress
from typing import Any
from uuid import UUID, uuid4

from aiohttp import web
from homeassistant.components.http import HomeAssistantView  # type: ignore[attr-defined]
from homeassistant.components.media_player import (  # type: ignore[attr-defined]
    MediaPlayerEntityFeature,
)
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_INSTALLATION_ID, DOMAIN
from .models import EkonexVoiceConfigEntry

LOCAL_MEDIA_API_PREFIX = "/api/evoice/media"


def register_local_media_api(hass: HomeAssistant) -> None:
    """Register the views once; loaded config entries are resolved per request."""
    key = f"{DOMAIN}_local_media_api_registered"
    if hass.data.get(key):
        return
    hass.data[key] = True
    hass.http.register_view(LocalMediaSnapshotView)
    hass.http.register_view(LocalMediaCommandView)
    hass.http.register_view(LocalMediaArtworkView)
    hass.http.register_view(LocalMediaEventsView)


def _loaded_entries(hass: HomeAssistant) -> list[EkonexVoiceConfigEntry]:
    result = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None and runtime.inventory is not None:
            result.append(entry)
    return result


def _exposed_items(
    hass: HomeAssistant,
) -> tuple[dict[str, tuple[EkonexVoiceConfigEntry, dict[str, object]]], list[dict[str, object]]]:
    registry = er.async_get(hass)
    players: dict[str, tuple[EkonexVoiceConfigEntry, dict[str, object]]] = {}
    related: dict[str, dict[str, object]] = {}
    for config_entry in _loaded_entries(hass):
        inventory = config_entry.runtime_data.inventory
        assert inventory is not None
        for entry in registry.entities.values():
            item = inventory._serialize(entry)
            if item is None:
                continue
            related.setdefault(entry.id, item)
            if entry.domain == "media_player":
                players.setdefault(entry.id, (config_entry, item))
    return players, list(related.values())


def _siblings(
    player: dict[str, object], related: Iterable[dict[str, object]]
) -> tuple[dict[str, object] | None, dict[str, object] | None, bool]:
    device_id = player.get("device_id")
    platform = str(player.get("platform") or "").lower()
    manufacturer = str(player.get("manufacturer") or "").lower()
    same_device = [item for item in related if device_id and item.get("device_id") == device_id]
    speech = next(
        (
            item
            for item in same_device
            if item.get("domain") == "notify"
            and str(item.get("entity_id")).endswith(("_speak", "_parla"))
        ),
        None,
    )
    dnd = next(
        (
            item
            for item in same_device
            if item.get("domain") == "switch"
            and str(item.get("entity_id")).endswith(
                ("_do_not_disturb", "_non_disturbare")
            )
        ),
        None,
    )
    return speech, dnd, manufacturer == "amazon" or platform in {
        "alexa_media",
        "alexa_devices",
    }


def _player_payload(
    config_entry: EkonexVoiceConfigEntry,
    item: dict[str, object],
    related: list[dict[str, object]],
) -> dict[str, object]:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    assert isinstance(attrs, dict)
    raw_features = item.get("supported_features")
    features = int(raw_features) if isinstance(raw_features, (int, str)) else 0
    volume = attrs.get("volume_level")
    speech, dnd, is_echo = _siblings(item, related)
    name = str(item.get("friendly_name") or item.get("entity_id"))
    return {
        "installation_id": str(config_entry.data.get(CONF_INSTALLATION_ID, "local")),
        "registry_id": item["registry_id"],
        "entity_id": item["entity_id"],
        "name": name,
        "room_id": item["registry_id"],
        "room_name": name,
        "experiences": item.get("experiences", []),
        "device_class": "echo" if is_echo else item.get("device_class"),
        "is_echo": is_echo,
        "manufacturer": item.get("manufacturer"),
        "model": item.get("model"),
        "state": item.get("state"),
        "availability": "available" if item.get("available") else "unavailable",
        "volume_percent": round(float(volume) * 100)
        if isinstance(volume, (int, float)) and not isinstance(volume, bool)
        else None,
        "muted": attrs.get("is_volume_muted"),
        "media": {
            "title": attrs.get("media_title"),
            "artist": attrs.get("media_artist"),
            "album": attrs.get("media_album_name"),
            "duration_seconds": attrs.get("media_duration"),
            "position_seconds": attrs.get("media_position"),
        },
        "source": attrs.get("source"),
        "source_list": attrs.get("source_list") or [],
        "group_members": attrs.get("group_members") or [],
        "capabilities": {
            "play": bool(features & int(MediaPlayerEntityFeature.PLAY)),
            "pause": bool(features & int(MediaPlayerEntityFeature.PAUSE)),
            "stop": bool(features & int(MediaPlayerEntityFeature.STOP)),
            "next": bool(features & int(MediaPlayerEntityFeature.NEXT_TRACK)),
            "previous": bool(features & int(MediaPlayerEntityFeature.PREVIOUS_TRACK)),
            "set_volume": bool(features & int(MediaPlayerEntityFeature.VOLUME_SET)),
            "mute": bool(features & int(MediaPlayerEntityFeature.VOLUME_MUTE)),
            "select_source": bool(features & int(MediaPlayerEntityFeature.SELECT_SOURCE)),
            "grouping": bool(features & int(MediaPlayerEntityFeature.GROUPING)),
            "tts": speech is not None and bool(speech.get("available")),
            "do_not_disturb": dnd is not None and bool(dnd.get("available")),
        },
        "dnd": dnd.get("state") == "on" if dnd is not None else None,
        "supported_features": features,
        "last_changed_at": item.get("last_changed_at"),
        "last_updated_at": item.get("last_updated_at"),
    }


def local_media_snapshot(hass: HomeAssistant) -> dict[str, object]:
    """Return one stable record for every locally exposed media player."""
    players, related = _exposed_items(hass)
    values = [
        _player_payload(config_entry, item, related)
        for config_entry, item in players.values()
    ]
    values.sort(key=lambda item: str(item["name"]).casefold())
    return {"api_version": "v1", "players": values, "player_count": len(values)}


def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


class LocalMediaSnapshotView(HomeAssistantView):
    url = f"{LOCAL_MEDIA_API_PREFIX}/snapshot"
    name = "api:ekonex_voice:media:snapshot"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        return web.json_response(local_media_snapshot(request.app["hass"]))


class LocalMediaCommandView(HomeAssistantView):
    url = f"{LOCAL_MEDIA_API_PREFIX}/players/{{registry_id}}/commands"
    name = "api:ekonex_voice:media:command"
    requires_auth = True

    async def post(self, request: web.Request, registry_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
            request_id = str(UUID(str(body["request_id"])))
            operation = str(body["operation"])
            arguments = body.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return _json_error(422, "INVALID_REQUEST", "Richiesta comando non valida")
        players, related = _exposed_items(hass)
        target = players.get(registry_id)
        if target is None:
            return _json_error(404, "PLAYER_NOT_FOUND", "Player non esposto")
        config_entry, player = target
        executor = config_entry.runtime_data.command_executor
        if executor is None:
            return _json_error(503, "EXECUTOR_UNAVAILABLE", "Comandi locali non disponibili")
        target_registry_id, command = registry_id, {"operation": operation, **arguments}
        if operation in {"tts", "set_dnd"}:
            speech, dnd, is_echo = _siblings(player, related)
            sibling = speech if operation == "tts" else dnd
            if not is_echo or sibling is None or not sibling.get("available"):
                return _json_error(
                    422, "UNSUPPORTED_COMMAND", "Funzione Echo non disponibile o non esposta"
                )
            target_registry_id = str(sibling["registry_id"])
            if operation == "tts":
                text = arguments.get("text")
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
                    return _json_error(422, "INVALID_ARGUMENT", "Testo TTS non valido")
                command = {"operation": "speak", "message": text.strip()}
            else:
                enabled = arguments.get("enabled")
                if type(enabled) is not bool:
                    return _json_error(422, "INVALID_ARGUMENT", "Valore DND non valido")
                command = {"operation": "power_on" if enabled else "power_off"}
        result = await executor.async_execute(request_id, target_registry_id, command)
        status = 200 if result.status == "success" else 422
        return web.json_response(
            {
                "request_id": request_id,
                "registry_id": registry_id,
                "operation": operation,
                "status": result.status,
                "error": {"code": result.error_code} if result.error_code else None,
                "response": result.response_data,
            },
            status=status,
        )


class LocalMediaArtworkView(HomeAssistantView):
    url = f"{LOCAL_MEDIA_API_PREFIX}/players/{{registry_id}}/artwork"
    name = "api:ekonex_voice:media:artwork"
    requires_auth = True

    async def get(self, request: web.Request, registry_id: str) -> web.Response:
        players, _ = _exposed_items(request.app["hass"])
        target = players.get(registry_id)
        if target is None or target[0].runtime_data.command_executor is None:
            return _json_error(404, "PLAYER_NOT_FOUND", "Player non esposto")
        result = await target[0].runtime_data.command_executor.async_execute(
            str(uuid4()), registry_id, {"operation": "media_artwork"}
        )
        if result.status != "success" or result.response_data is None:
            return _json_error(404, result.error_code or "ARTWORK_NOT_FOUND", "Copertina assente")
        try:
            image = base64.b64decode(
                str(result.response_data["image_base64"]), validate=True
            )
            content_type = str(result.response_data["content_type"]).split(";", 1)[0]
        except (KeyError, TypeError, ValueError):
            return _json_error(502, "INVALID_ARTWORK", "Copertina non valida")
        return web.Response(body=image, content_type=content_type)


class LocalMediaEventsView(HomeAssistantView):
    url = f"{LOCAL_MEDIA_API_PREFIX}/events"
    name = "api:ekonex_voice:media:events"
    requires_auth = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)

        @callback
        def changed(event: Event[Any]) -> None:
            entity_id = event.data.get("entity_id")
            if not isinstance(entity_id, str):
                return
            _, related = _exposed_items(hass)
            if entity_id not in {str(item.get("entity_id")) for item in related}:
                return
            with suppress(asyncio.QueueFull):
                queue.put_nowait(entity_id)

        unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, changed)
        try:
            while True:
                try:
                    entity_id = await asyncio.wait_for(queue.get(), timeout=15)
                    await asyncio.sleep(0.25)
                    payload = local_media_snapshot(hass)
                    event = {"type": "player.updated", "entity_id": entity_id, **payload}
                except TimeoutError:
                    event = {"type": "heartbeat"}
                data = json.dumps(event, separators=(",", ":"))
                await response.write(f"event: {event['type']}\ndata: {data}\n\n".encode())
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            unsubscribe()
        return response
