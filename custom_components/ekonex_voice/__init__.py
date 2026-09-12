"""Home Assistant lifecycle for Ekonex Voice."""

from __future__ import annotations

from homeassistant.const import __version__ as ha_version
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .announcement import register_announcement_service
from .client import (
    EkonexVoiceAuthError,
    EkonexVoiceCannotConnect,
    EkonexVoiceClient,
    EkonexVoiceProtocolError,
)
from .command_executor import EkonexVoiceCommandExecutor
from .connection import EkonexVoiceConnection
from .const import (
    CONF_CLOUD_URL,
    CONF_CONNECTOR_CREDENTIAL,
    CONF_EXPOSED_DEVICE_IDS,
    CONF_EXPOSED_ENTITY_REGISTRY_IDS,
    CONF_EXPOSURE_LABEL_ID,
    CONF_INSTALLATION_ID,
    CONF_MEDIA_EXPERIENCES,
    DOMAIN,
)
from .entity_inventory import EntityInventorySynchronizer
from .local_media_api import register_local_media_api
from .models import EkonexVoiceConfigEntry, EkonexVoiceRuntimeData

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict[str, object]) -> bool:
    """Register local automation actions without opening another connection."""
    register_announcement_service(hass)
    register_local_media_api(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EkonexVoiceConfigEntry) -> bool:
    """Validate and set up one cloud Connector entry."""
    integration = await async_get_integration(hass, DOMAIN)
    if integration.version is None:
        raise ConfigEntryNotReady("connector_version_unavailable")
    connector_version = str(integration.version)
    client = EkonexVoiceClient(
        async_get_clientsession(hass),
        str(entry.data[CONF_CLOUD_URL]),
        connector_credential=str(entry.data[CONF_CONNECTOR_CREDENTIAL]),
    )
    expected_installation = str(entry.data[CONF_INSTALLATION_ID])
    try:
        authenticated_installation = await client.async_authenticate()
    except EkonexVoiceAuthError as error:
        raise ConfigEntryAuthFailed("invalid_auth") from error
    except EkonexVoiceCannotConnect as error:
        raise ConfigEntryNotReady("cloud_unavailable") from error
    except EkonexVoiceProtocolError:
        await client.async_close()
        raise
    if authenticated_installation != expected_installation:
        await client.async_close()
        raise ConfigEntryAuthFailed("installation_identity_mismatch")

    inventory = EntityInventorySynchronizer(
        hass,
        set(entry.options.get(CONF_EXPOSED_DEVICE_IDS, [])),
        set(entry.options.get(CONF_EXPOSED_ENTITY_REGISTRY_IDS, [])),
        entry.options.get(CONF_EXPOSURE_LABEL_ID),
        entry.options.get(CONF_MEDIA_EXPERIENCES, {}),
    )
    command_executor = EkonexVoiceCommandExecutor(hass, inventory)
    connection = EkonexVoiceConnection(
        hass,
        client.async_connect_websocket,
        expected_installation,
        connector_version=connector_version,
        ha_version=ha_version,
        on_auth_failure=lambda: entry.async_start_reauth(hass),
        inventory=inventory,
        command_executor=command_executor,
    )
    entry.runtime_data = EkonexVoiceRuntimeData(
        client=client,
        connection=connection,
        inventory=inventory,
        command_executor=command_executor,
    )
    connection.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EkonexVoiceConfigEntry) -> bool:
    """Unload deterministically without leaving tasks or sessions behind."""
    await entry.runtime_data.async_close()
    return True
