"""UI-only config, pairing and reauthentication flows for Ekonex Voice."""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    LabelSelector,
    LabelSelectorConfig,
)

from .client import (
    EkonexVoiceAuthError,
    EkonexVoiceCannotConnect,
    EkonexVoiceClient,
    EkonexVoicePairingDenied,
    EkonexVoicePairingExpired,
    EkonexVoiceProtocolError,
)
from .const import (
    CONF_CLOUD_URL,
    CONF_CONNECTOR_CREDENTIAL,
    CONF_EXPOSED_DEVICE_IDS,
    CONF_EXPOSED_ENTITY_REGISTRY_IDS,
    CONF_EXPOSURE_LABEL_ID,
    CONF_INSTALLATION_ID,
    CONF_INSTALLATION_NAME,
    CONF_TENANT_NAME,
    DEFAULT_CLOUD_URL,
    DOMAIN,
)
from .models import PairingResult, PairingSession, PairingState


class EkonexVoiceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Pair one Ekonex installation without persisting transient secrets."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: EkonexVoiceClient | None = None
        self._pairing: PairingSession | None = None

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EkonexVoiceOptionsFlow:
        return EkonexVoiceOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start pairing immediately from Add Integration."""
        return await self._async_start_pairing()

    async def async_step_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Poll only when the user asks Home Assistant to check the claim."""
        if self._pairing is None:
            return self.async_abort(reason="unknown")
        if user_input is None:
            return self._show_pairing_form()
        try:
            result = await self._get_client().async_poll_pairing_session(
                self._pairing.session_id, self._pairing.polling_secret
            )
        except EkonexVoicePairingExpired:
            return self.async_abort(reason="pairing_expired")
        except EkonexVoicePairingDenied:
            return self.async_abort(reason="pairing_denied")
        except EkonexVoiceCannotConnect:
            return self._show_pairing_form(errors={"base": "cannot_connect"})
        except EkonexVoiceAuthError:
            return self.async_abort(reason="invalid_auth")
        except EkonexVoiceProtocolError:
            return self.async_abort(reason="unknown")
        if result.state is PairingState.PENDING:
            return self._show_pairing_form(errors={"base": "pairing_pending"})
        return await self._async_finish_pairing(result)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start a ConfigEntry-linked credential replacement."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require an explicit user action before showing a new human code."""
        if user_input is not None:
            return await self._async_start_pairing()
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({}))

    async def _async_start_pairing(self) -> ConfigFlowResult:
        self._client = EkonexVoiceClient(async_get_clientsession(self.hass), DEFAULT_CLOUD_URL)
        try:
            self._pairing = await self._client.async_create_pairing_session(
                f"haos_{secrets.token_urlsafe(24)}"
            )
        except EkonexVoiceCannotConnect:
            return self.async_abort(reason="cannot_connect")
        except (EkonexVoiceAuthError, EkonexVoiceProtocolError):
            return self.async_abort(reason="unknown")
        return await self.async_step_pairing()

    async def _async_finish_pairing(self, result: PairingResult) -> ConfigFlowResult:
        if result.installation_id is None or result.connector_credential is None:
            return self.async_abort(reason="unknown")
        await self.async_set_unique_id(result.installation_id)
        data = {
            CONF_CLOUD_URL: DEFAULT_CLOUD_URL,
            CONF_INSTALLATION_ID: result.installation_id,
            CONF_CONNECTOR_CREDENTIAL: result.connector_credential,
            CONF_INSTALLATION_NAME: result.installation_name or "Ekonex Voice",
            CONF_TENANT_NAME: result.tenant_name or "Ekonex",
        }
        if self.source == config_entries.SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_installation")
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{data[CONF_TENANT_NAME]} / {data[CONF_INSTALLATION_NAME]}",
            data=data,
            options={CONF_EXPOSED_DEVICE_IDS: [], CONF_EXPOSED_ENTITY_REGISTRY_IDS: []},
        )

    def _get_client(self) -> EkonexVoiceClient:
        if self._client is None:
            raise RuntimeError("pairing client is unavailable")
        return self._client

    def _show_pairing_form(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        if self._pairing is None:
            return self.async_abort(reason="unknown")
        return self.async_show_form(
            step_id="pairing",
            data_schema=vol.Schema({}),
            errors=errors or {},
            description_placeholders={
                "code": self._pairing.code,
                "expires_at": self._pairing.expires_at.isoformat(timespec="minutes"),
                "pairing_url": "https://voice.e-control.tech/pair",
            },
        )


class EkonexVoiceOptionsFlow(config_entries.OptionsFlowWithReload):
    """Manage the explicit opt-in exposure set and reload immediately."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        registry = er.async_get(self.hass)
        if user_input is not None:
            label_id = user_input.get("label")
            registry_ids = [
                entry.id
                for entity_id in user_input.get("entities", [])
                if (entry := registry.async_get(entity_id)) is not None
            ]
            data: dict[str, Any] = {
                CONF_EXPOSED_DEVICE_IDS: sorted(user_input.get("devices", [])),
                CONF_EXPOSED_ENTITY_REGISTRY_IDS: sorted(registry_ids),
            }
            if label_id:
                data[CONF_EXPOSURE_LABEL_ID] = label_id
            return self.async_create_entry(data=data)
        return self._show_form(registry)

    def _show_form(
        self, registry: er.EntityRegistry, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        selected = set(self.config_entry.options.get(CONF_EXPOSED_ENTITY_REGISTRY_IDS, []))
        entity_ids = sorted(
            entry.entity_id for entry in registry.entities.values() if entry.id in selected
        )
        current_label = self.config_entry.options.get(CONF_EXPOSURE_LABEL_ID)
        schema = vol.Schema(
            {
                vol.Optional(
                    "devices",
                    description={
                        "suggested_value": list(
                            self.config_entry.options.get(CONF_EXPOSED_DEVICE_IDS, [])
                        )
                    },
                ): DeviceSelector(DeviceSelectorConfig(multiple=True)),
                vol.Optional(
                    "entities", description={"suggested_value": entity_ids}
                ): EntitySelector(EntitySelectorConfig(multiple=True)),
                vol.Optional(
                    "label", description={"suggested_value": current_label}
                ): LabelSelector(LabelSelectorConfig(multiple=False)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors or {})
