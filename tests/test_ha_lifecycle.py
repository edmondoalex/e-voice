"""Tests for ConfigEntry lifecycle and cleanup."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ekonex_voice.client import (
    EkonexVoiceAuthError,
    EkonexVoiceCannotConnect,
)
from custom_components.ekonex_voice.const import (
    CONF_CLOUD_URL,
    CONF_CONNECTOR_CREDENTIAL,
    CONF_INSTALLATION_ID,
    DEFAULT_CLOUD_URL,
    DOMAIN,
)

INSTALLATION_ID = "installation-1"
CONNECTOR_VERSION = json.loads(
    Path("custom_components/ekonex_voice/manifest.json").read_text(encoding="utf-8")
)["version"]


def config_entry() -> MockConfigEntry:
    """Build a valid Connector entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=INSTALLATION_ID,
        data={
            CONF_CLOUD_URL: DEFAULT_CLOUD_URL,
            CONF_INSTALLATION_ID: INSTALLATION_ID,
            CONF_CONNECTOR_CREDENTIAL: "connector-canary",
        },
    )


async def test_setup_and_unload_close_owned_resources(hass: HomeAssistant) -> None:
    """Unload stops the supervisor and closes the client exactly once."""
    entry = config_entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_authenticate = AsyncMock(return_value=INSTALLATION_ID)
    client.async_close = AsyncMock()
    connection = MagicMock()
    connection.async_stop = AsyncMock()
    connection_factory = MagicMock(return_value=connection)
    with (
        patch("custom_components.ekonex_voice.EkonexVoiceClient", return_value=client),
        patch(
            "custom_components.ekonex_voice.EkonexVoiceConnection",
            connection_factory,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state is ConfigEntryState.LOADED
        connection.async_start.assert_called_once_with()
        assert connection_factory.call_args.kwargs["connector_version"] == CONNECTOR_VERSION
        assert await hass.config_entries.async_unload(entry.entry_id)

    connection.async_stop.assert_awaited_once_with()
    client.async_close.assert_awaited_once_with()
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("error", "expected_state"),
    [
        (EkonexVoiceAuthError("secret-bearing-detail"), ConfigEntryState.SETUP_ERROR),
        (EkonexVoiceCannotConnect("secret-bearing-detail"), ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_maps_safe_failure_states(
    hass: HomeAssistant, error: Exception, expected_state: ConfigEntryState
) -> None:
    """Auth and transient errors use Home Assistant lifecycle machinery."""
    entry = config_entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_authenticate = AsyncMock(side_effect=error)
    with patch("custom_components.ekonex_voice.EkonexVoiceClient", return_value=client):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is expected_state


async def test_identity_mismatch_never_loads(hass: HomeAssistant) -> None:
    """Authentication for another installation is handled as invalid auth."""
    entry = config_entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_authenticate = AsyncMock(return_value="different-installation")
    client.async_close = AsyncMock()
    with patch("custom_components.ekonex_voice.EkonexVoiceClient", return_value=client):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    client.async_close.assert_awaited_once_with()
    assert entry.state is ConfigEntryState.SETUP_ERROR
