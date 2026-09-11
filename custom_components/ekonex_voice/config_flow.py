"""UI-only config, pairing and reauthentication flows for Ekonex Voice."""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
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
    CONF_MEDIA_EXPERIENCES,
    CONF_TENANT_NAME,
    DEFAULT_CLOUD_URL,
    DOMAIN,
    LABORATORY_CLOUD_URL,
    MEDIA_EXPERIENCE_LISTEN,
    MEDIA_EXPERIENCE_WATCH,
)
from .models import PairingResult, PairingSession, PairingState


class EkonexVoiceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Pair one Ekonex installation without persisting transient secrets."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: EkonexVoiceClient | None = None
        self._pairing: PairingSession | None = None
        self._cloud_url = DEFAULT_CLOUD_URL

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EkonexVoiceOptionsFlow:
        return EkonexVoiceOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the isolated endpoint before starting pairing."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required("environment", default="production"): vol.In(
                            {"production": "Produzione", "laboratory": "Laboratorio"}
                        )
                    }
                ),
            )
        self._cloud_url = (
            LABORATORY_CLOUD_URL
            if user_input["environment"] == "laboratory"
            else DEFAULT_CLOUD_URL
        )
        return await self._async_start_pairing(self._cloud_url)

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
            cloud_url = str(self._get_reauth_entry().data.get(CONF_CLOUD_URL, DEFAULT_CLOUD_URL))
            self._cloud_url = cloud_url
            return await self._async_start_pairing(cloud_url)
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({}))

    async def _async_start_pairing(self, cloud_url: str) -> ConfigFlowResult:
        self._client = EkonexVoiceClient(async_get_clientsession(self.hass), cloud_url)
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
            CONF_CLOUD_URL: self._cloud_url,
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
            options={
                CONF_EXPOSED_DEVICE_IDS: [],
                CONF_EXPOSED_ENTITY_REGISTRY_IDS: [],
                CONF_MEDIA_EXPERIENCES: {},
            },
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
                "pairing_url": f"{self._cloud_url}/pair",
            },
        )


class EkonexVoiceOptionsFlow(config_entries.OptionsFlowWithReload):
    """Manage the explicit opt-in exposure set and reload immediately."""

    def __init__(self) -> None:
        self._pending_options: dict[str, Any] | None = None

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
            self._pending_options = data
            if self._exposed_media_entries(data):
                return await self.async_step_media_experiences()
            data[CONF_MEDIA_EXPERIENCES] = {}
            return self.async_create_entry(data=data)
        return self._show_form(registry)

    async def async_step_media_experiences(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Classify exposed media players for the Watch and Listen experiences."""
        if self._pending_options is None:
            return await self.async_step_init()
        entries = self._exposed_media_entries(self._pending_options)
        by_entity_id = {entry.entity_id: entry for entry in entries}
        if user_input is not None:
            exposed_ids = set(by_entity_id)
            watch = set(user_input.get("watch_players", [])) & exposed_ids
            listen = set(user_input.get("listen_players", [])) & exposed_ids
            if exposed_ids - watch - listen:
                return self._show_media_form(entries, {"base": "media_experience_required"})
            experiences: dict[str, list[str]] = {}
            for entity_id, entry in by_entity_id.items():
                values: list[str] = []
                if entity_id in watch:
                    values.append(MEDIA_EXPERIENCE_WATCH)
                if entity_id in listen:
                    values.append(MEDIA_EXPERIENCE_LISTEN)
                experiences[entry.id] = values
            self._pending_options[CONF_MEDIA_EXPERIENCES] = experiences
            return self.async_create_entry(data=self._pending_options)
        return self._show_media_form(entries)

    def _exposed_media_entries(self, options: dict[str, Any]) -> list[er.RegistryEntry]:
        registry = er.async_get(self.hass)
        devices = dr.async_get(self.hass)
        selected_entities = set(options.get(CONF_EXPOSED_ENTITY_REGISTRY_IDS, []))
        selected_devices = set(options.get(CONF_EXPOSED_DEVICE_IDS, []))
        label_id = options.get(CONF_EXPOSURE_LABEL_ID)
        result: list[er.RegistryEntry] = []
        for entry in registry.entities.values():
            device = devices.async_get(entry.device_id) if entry.device_id else None
            exposed = (
                entry.id in selected_entities
                or entry.device_id in selected_devices
                or (
                    label_id is not None
                    and (
                        label_id in entry.labels
                        or (device is not None and label_id in device.labels)
                    )
                )
            )
            if exposed and entry.domain == "media_player" and not entry.disabled:
                result.append(entry)
        return sorted(result, key=lambda item: item.entity_id)

    def _show_media_form(
        self, entries: list[er.RegistryEntry], errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        current = self.config_entry.options.get(CONF_MEDIA_EXPERIENCES, {})
        watch: list[str] = []
        listen: list[str] = []
        for entry in entries:
            configured = current.get(entry.id) if isinstance(current, dict) else None
            values = (
                configured
                if isinstance(configured, list)
                else _suggest_experiences(self.hass, entry)
            )
            if MEDIA_EXPERIENCE_WATCH in values:
                watch.append(entry.entity_id)
            if MEDIA_EXPERIENCE_LISTEN in values:
                listen.append(entry.entity_id)
        selector = EntitySelectorConfig(domain="media_player", multiple=True)
        schema = vol.Schema(
            {
                vol.Optional(
                    "watch_players", description={"suggested_value": watch}
                ): EntitySelector(selector),
                vol.Optional(
                    "listen_players", description={"suggested_value": listen}
                ): EntitySelector(selector),
            }
        )
        return self.async_show_form(
            step_id="media_experiences", data_schema=schema, errors=errors or {}
        )

    def _show_form(
        self, registry: er.EntityRegistry, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        selected = set(self.config_entry.options.get(CONF_EXPOSED_ENTITY_REGISTRY_IDS, []))
        entity_ids = sorted(
            entry.entity_id for entry in registry.entities.values() if entry.id in selected
        )
        current_label = self.config_entry.options.get(CONF_EXPOSURE_LABEL_ID)
        label_options = [
            SelectOptionDict(value=label.label_id, label=label.name)
            for label in sorted(
                lr.async_get(self.hass).async_list_labels(),
                key=lambda item: item.name.casefold(),
            )
        ]
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
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=label_options,
                        multiple=False,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors or {})


def _suggest_experiences(hass: HomeAssistant, entry: er.RegistryEntry) -> list[str]:
    """Suggest a conservative default; a saved manual choice always wins."""
    state = hass.states.get(entry.entity_id)
    device_class = str(
        entry.device_class
        or entry.original_device_class
        or (state.attributes.get("device_class") if state else "")
        or ""
    ).lower()
    platform = str(entry.platform or "").lower()
    if device_class in {"tv", "streaming_stick", "game_console"}:
        return [MEDIA_EXPERIENCE_WATCH]
    if device_class == "receiver":
        return [MEDIA_EXPERIENCE_WATCH, MEDIA_EXPERIENCE_LISTEN]
    if device_class == "speaker" or platform in {"alexa_media", "sonos", "snapcast"}:
        return [MEDIA_EXPERIENCE_LISTEN]
    return [MEDIA_EXPERIENCE_LISTEN]
