"""Tests for recursive secret-safe diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ekonex_voice.const import (
    CONF_CLOUD_URL,
    CONF_CONNECTOR_CREDENTIAL,
    CONF_INSTALLATION_ID,
    DOMAIN,
    REDACTED,
)
from custom_components.ekonex_voice.diagnostics import (
    _redact_recursive,
    async_get_config_entry_diagnostics,
)
from custom_components.ekonex_voice.models import ConnectionState, EkonexVoiceRuntimeData

CANARY = "never-leak-this-canary"


async def test_diagnostics_are_bounded_and_redact_entry_credential(
    hass: HomeAssistant,
) -> None:
    """Only safe entry and connection health fields are emitted."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="installation-1",
        data={
            CONF_CLOUD_URL: "https://api.ekonex.it",
            CONF_INSTALLATION_ID: "installation-1",
            CONF_CONNECTOR_CREDENTIAL: CANARY,
        },
    )
    connection = MagicMock(
        state=ConnectionState.ONLINE,
        retry_count=0,
        next_retry_delay=None,
        last_error_code=None,
        last_connected_at=datetime.now(UTC),
    )
    entry.runtime_data = EkonexVoiceRuntimeData(client=MagicMock(), connection=connection)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert CANARY not in repr(diagnostics)
    assert diagnostics["entry"][CONF_CONNECTOR_CREDENTIAL] == REDACTED
    assert diagnostics["connection"]["state"] is ConnectionState.ONLINE
    assert set(diagnostics) == {"entry", "entry_version", "connection", "exposure"}
    assert diagnostics["exposure"] == {
        "ui_device_count": 0,
        "ui_entity_count": 0,
        "label_configured": False,
        "label_id": None,
        "last_full_revision": None,
        "last_full_entity_count": None,
        "last_state_entity_count": None,
        "send_failure_count": 0,
        "last_error_code": None,
    }


def test_recursive_redaction_handles_nested_values_and_url_queries() -> None:
    """Nested secrets and credential-bearing query parameters are removed."""
    value = {
        "outer": [{"refresh_token": CANARY}],
        "url": f"https://user:{CANARY}@example.test/path?token={CANARY}&safe=yes#fragment",
        "credential_envelope": {"nested": CANARY},
    }

    redacted = _redact_recursive(value)

    assert CANARY not in repr(redacted)
    assert redacted["outer"][0]["refresh_token"] == REDACTED
    assert redacted["credential_envelope"] == REDACTED
    assert redacted["url"] == f"https://example.test/path?token={REDACTED}&safe=yes"
