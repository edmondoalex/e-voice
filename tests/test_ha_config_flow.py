"""Tests for the Ekonex Voice config flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ekonex_voice.client import (
    EkonexVoiceCannotConnect,
    EkonexVoicePairingDenied,
    EkonexVoicePairingExpired,
)
from custom_components.ekonex_voice.const import (
    CONF_CLOUD_URL,
    CONF_CONNECTOR_CREDENTIAL,
    CONF_INSTALLATION_ID,
    CONF_INSTALLATION_NAME,
    CONF_TENANT_NAME,
    DEFAULT_CLOUD_URL,
    DOMAIN,
)
from custom_components.ekonex_voice.models import PairingResult, PairingSession, PairingState

INSTALLATION_ID = "94e98b32-975f-4e7d-b560-c3543dad02ec"
PAIRING_CODE = "ABCD-1234"
POLLING_SECRET = "polling-canary-secret"
CONNECTOR_SECRET = "connector-canary-secret"


def pairing_session() -> PairingSession:
    """Return transient pairing data."""
    return PairingSession(
        session_id="session-1",
        code=PAIRING_CODE,
        polling_secret=POLLING_SECRET,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


def claimed_result(installation_id: str = INSTALLATION_ID) -> PairingResult:
    """Return a successful M2 claim."""
    return PairingResult(
        state=PairingState.CLAIMED,
        installation_id=installation_id,
        connector_credential=CONNECTOR_SECRET,
        installation_name="Home",
        tenant_name="Villa Rossi",
    )


async def start_flow(hass: HomeAssistant, client: AsyncMock) -> data_entry_flow.FlowResult:
    """Start a user flow with a patched client instance."""
    client.async_create_pairing_session.return_value = pairing_session()
    with patch(
        "custom_components.ekonex_voice.config_flow.EkonexVoiceClient",
        return_value=client,
    ):
        return await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )


async def test_success_stores_only_durable_claim_material(hass: HomeAssistant) -> None:
    """Human and polling credentials never enter ConfigEntry data."""
    client = AsyncMock()
    result = await start_flow(hass, client)
    client.async_poll_pairing_session.return_value = claimed_result()

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Villa Rossi / Home"
    assert result["data"] == {
        CONF_CLOUD_URL: DEFAULT_CLOUD_URL,
        CONF_INSTALLATION_ID: INSTALLATION_ID,
        CONF_CONNECTOR_CREDENTIAL: CONNECTOR_SECRET,
        CONF_INSTALLATION_NAME: "Home",
        CONF_TENANT_NAME: "Villa Rossi",
    }
    serialized = repr(result["data"])
    assert PAIRING_CODE not in serialized
    assert POLLING_SECRET not in serialized


async def test_pairing_form_displays_only_intended_human_code(hass: HomeAssistant) -> None:
    """The UI receives the code and expiry, never the polling secret."""
    result = await start_flow(hass, AsyncMock())

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "pairing"
    assert result["description_placeholders"]["code"] == PAIRING_CODE
    assert POLLING_SECRET not in repr(result)


async def test_pending_claim_can_be_checked_again(hass: HomeAssistant) -> None:
    """A pending claim remains in the same bounded flow."""
    client = AsyncMock()
    result = await start_flow(hass, client)
    client.async_poll_pairing_session.return_value = PairingResult(PairingState.PENDING)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "pairing_pending"}


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (EkonexVoicePairingExpired("safe"), "pairing_expired"),
        (EkonexVoicePairingDenied("safe"), "pairing_denied"),
    ],
)
async def test_terminal_pairing_failures_abort_safely(
    hass: HomeAssistant, error: Exception, reason: str
) -> None:
    """Expiry and denial have stable translated abort reasons."""
    client = AsyncMock()
    result = await start_flow(hass, client)
    client.async_poll_pairing_session.side_effect = error

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == reason


async def test_connectivity_failure_is_retryable_without_secret_leak(
    hass: HomeAssistant,
) -> None:
    """A poll connectivity failure returns a safe form error."""
    client = AsyncMock()
    result = await start_flow(hass, client)
    client.async_poll_pairing_session.side_effect = EkonexVoiceCannotConnect(CONNECTOR_SECRET)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["errors"] == {"base": "cannot_connect"}
    assert CONNECTOR_SECRET not in repr(result)


async def test_duplicate_installation_is_aborted(hass: HomeAssistant) -> None:
    """Cloud installation identity prevents duplicate entries."""
    MockConfigEntry(domain=DOMAIN, unique_id=INSTALLATION_ID, data={}).add_to_hass(hass)
    client = AsyncMock()
    result = await start_flow(hass, client)
    client.async_poll_pairing_session.return_value = claimed_result()

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_same_entry(hass: HomeAssistant) -> None:
    """Reauth replaces only durable data and reloads the linked entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=INSTALLATION_ID,
        data={
            CONF_CLOUD_URL: DEFAULT_CLOUD_URL,
            CONF_INSTALLATION_ID: INSTALLATION_ID,
            CONF_CONNECTOR_CREDENTIAL: "old-secret",
            CONF_INSTALLATION_NAME: "Home",
            CONF_TENANT_NAME: "Villa Rossi",
        },
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_create_pairing_session.return_value = pairing_session()
    client.async_poll_pairing_session.return_value = claimed_result()
    with patch(
        "custom_components.ekonex_voice.config_flow.EkonexVoiceClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": INSTALLATION_ID,
            },
            data=dict(entry.data),
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_CONNECTOR_CREDENTIAL] == CONNECTOR_SECRET
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauth_rejects_different_installation(hass: HomeAssistant) -> None:
    """A credential cannot silently move an entry to another installation."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=INSTALLATION_ID, data={})
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_create_pairing_session.return_value = pairing_session()
    client.async_poll_pairing_session.return_value = claimed_result("other-installation")
    with patch(
        "custom_components.ekonex_voice.config_flow.EkonexVoiceClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": INSTALLATION_ID,
            },
            data={},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "wrong_installation"
