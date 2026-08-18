"""Bounded, recursively redacted diagnostics for Ekonex Voice."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import (
    CONF_CONNECTOR_CREDENTIAL,
    REDACTED,
)
from .models import EkonexVoiceConfigEntry

_SENSITIVE_KEYS = {
    CONF_CONNECTOR_CREDENTIAL,
    "authorization",
    "code",
    "cookie",
    "credential_envelope",
    "pairing_code",
    "password",
    "polling_secret",
    "refresh_token",
    "token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EkonexVoiceConfigEntry
) -> dict[str, Any]:
    """Return safe health facts, never raw protocol or personal data."""
    connection = entry.runtime_data.connection
    entry_data = async_redact_data(dict(entry.data), {CONF_CONNECTOR_CREDENTIAL})
    return cast(
        dict[str, Any],
        _redact_recursive(
            {
                "entry": entry_data,
                "entry_version": entry.version,
                "connection": {
                    "state": connection.state,
                    "retry_count": connection.retry_count,
                    "next_retry_delay": connection.next_retry_delay,
                    "last_error_code": connection.last_error_code,
                    "last_connected_at": connection.last_connected_at,
                },
                "exposure": (
                    entry.runtime_data.inventory.exposure_summary
                    if entry.runtime_data.inventory is not None
                    else {
                        "ui_device_count": 0,
                        "ui_entity_count": 0,
                        "label_configured": False,
                        "label_id": None,
                    }
                ),
            }
        ),
    )


def _redact_recursive(value: Any, key: str | None = None) -> Any:
    """Redact sensitive keys at any depth and query credentials in URLs."""
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_recursive(item, str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_recursive(item) for item in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return _redact_url(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized in _SENSITIVE_KEYS or any(
        fragment in normalized for fragment in ("password", "secret", "token", "credential")
    )


def _redact_url(value: str) -> str:
    parts = urlsplit(value)
    query = urlencode(
        [
            (key, REDACTED if _is_sensitive_key(key) else item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
        ],
        safe="*",
    )
    hostname = parts.hostname or ""
    if parts.port is not None:
        hostname = f"{hostname}:{parts.port}"
    return urlunsplit((parts.scheme, hostname, parts.path, query, ""))
