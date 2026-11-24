"""Single-owner e-Control exposure API tests."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ekonex_voice.const import (
    CONF_EXPOSED_DEVICE_IDS,
    CONF_EXPOSED_ENTITY_REGISTRY_IDS,
    CONF_EXPOSURE_LABEL_ID,
    CONF_MEDIA_EXPERIENCES,
    DOMAIN,
)
from custom_components.ekonex_voice.exposure_api import _catalog, _entry, _updated_options


async def test_default_entry_uses_only_media_configured_instance(
    hass: HomeAssistant,
) -> None:
    primary = MockConfigEntry(
        domain=DOMAIN,
        options={CONF_MEDIA_EXPERIENCES: {}},
    )
    laboratory = MockConfigEntry(domain=DOMAIN, options={})
    primary.add_to_hass(hass)
    laboratory.add_to_hass(hass)

    assert _entry(hass, None) is primary


async def test_catalog_projects_existing_component_selection(hass: HomeAssistant) -> None:
    component = MockConfigEntry(
        domain=DOMAIN,
        options={CONF_EXPOSED_DEVICE_IDS: []},
    )
    component.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=component.entry_id,
        identifiers={("test", "office")},
        name="Office",
    )
    entity = er.async_get(hass).async_get_or_create(
        "light", "test", "office-light", device_id=device.id
    )
    hass.config_entries.async_update_entry(
        component, options={CONF_EXPOSED_DEVICE_IDS: [device.id]}
    )
    hass.states.async_set(entity.entity_id, "on", {"friendly_name": "Luce ufficio"})

    catalog = _catalog(hass, component)

    assert catalog["configuration_owner"] == "e-control"
    assert catalog["selected_count"] == 1
    [item] = catalog["entities"]
    assert item["registry_id"] == entity.id
    assert item["selected"] is True
    assert item["name"] == "Luce ufficio"


def test_update_collapses_legacy_selectors_and_preserves_selected_media() -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_EXPOSED_DEVICE_IDS: ["device-old"],
            CONF_EXPOSED_ENTITY_REGISTRY_IDS: ["old"],
            CONF_EXPOSURE_LABEL_ID: "label-old",
            CONF_MEDIA_EXPERIENCES: {
                "keep": ["listen"],
                "remove": ["watch"],
            },
            "unrelated_option": True,
        },
    )

    options = _updated_options(entry, ["keep", "new"])

    assert options[CONF_EXPOSED_ENTITY_REGISTRY_IDS] == ["keep", "new"]
    assert options[CONF_EXPOSED_DEVICE_IDS] == []
    assert CONF_EXPOSURE_LABEL_ID not in options
    assert options[CONF_MEDIA_EXPERIENCES] == {"keep": ["listen"]}
    assert options["unrelated_option"] is True
