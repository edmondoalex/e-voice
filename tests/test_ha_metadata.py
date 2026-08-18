"""Tests for custom integration metadata and localization."""

from __future__ import annotations

import json
from pathlib import Path

INTEGRATION = Path("custom_components/ekonex_voice")


def test_manifest_is_haos_cloud_service_foundation() -> None:
    """Manifest uses the required current custom-integration keys."""
    manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["domain"] == "ekonex_voice"
    assert manifest["name"] == "Ekonex Voice"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "service"
    assert manifest["iot_class"] == "cloud_push"
    assert manifest["version"]
    assert manifest["requirements"] == []


def test_translation_catalogs_have_identical_structure_and_placeholders() -> None:
    """English and Italian must expose the same runtime translation contract."""
    english = json.loads((INTEGRATION / "translations/en.json").read_text(encoding="utf-8"))
    italian = json.loads((INTEGRATION / "translations/it.json").read_text(encoding="utf-8"))

    assert _shape(english) == _shape(italian)
    assert _placeholders(english) == _placeholders(italian)


def test_strings_json_is_not_used_by_custom_integration() -> None:
    """Current HA custom integrations load full translations directly."""
    assert not (INTEGRATION / "strings.json").exists()


def _shape(value: object) -> object:
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    return str


def _placeholders(value: object) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_placeholders(item) for item in value.values()), set())
    if not isinstance(value, str):
        return set()
    return {part.split("}", 1)[0] for part in value.split("{")[1:] if "}" in part}
