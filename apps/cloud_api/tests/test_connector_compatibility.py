"""Connector compatibility policy regression tests."""

import pytest

from apps.cloud_api.app import connector_compatibility as compatibility_module
from apps.cloud_api.app.connector_compatibility import (
    ConnectorCompatibilityStatus,
    connector_compatibility,
    effective_connector_capabilities,
)

ALL_CAPABILITIES = {
    "supports_correlation_id": True,
    "supports_command_diagnostics": True,
    "supports_heartbeat_diagnostics": True,
}


def test_connector_017_is_incompatible_with_current_cloud() -> None:
    result = connector_compatibility("0.1.7", [1], {})
    assert result.status is ConnectorCompatibilityStatus.INCOMPATIBLE
    assert result.reason == "required_connector_capabilities_missing"


def test_connector_beta5_is_supported_with_update_available() -> None:
    result = connector_compatibility("0.1.8-beta.5", [1], ALL_CAPABILITIES)
    assert result.status is ConnectorCompatibilityStatus.UPDATE_AVAILABLE
    assert result.selected_protocol == 1


def test_released_beta5_capabilities_are_inferred_when_hello_has_no_declaration() -> None:
    capabilities = effective_connector_capabilities("0.1.8-beta.5", {})
    result = connector_compatibility("0.1.8-beta.5", [1], capabilities)
    assert capabilities == ALL_CAPABILITIES
    assert result.status is ConnectorCompatibilityStatus.UPDATE_AVAILABLE


def test_protocol_mismatch_is_incompatible() -> None:
    result = connector_compatibility("0.1.8-beta.5", [2], ALL_CAPABILITIES)
    assert result.status is ConnectorCompatibilityStatus.INCOMPATIBLE
    assert result.reason == "required_evcp_protocol_not_supported"


def test_offline_or_unknown_connector_has_unknown_status() -> None:
    result = connector_compatibility(None, None, None)
    assert result.status is ConnectorCompatibilityStatus.UNKNOWN_OFFLINE


def test_older_version_with_compatible_features_remains_unsupported() -> None:
    result = connector_compatibility("0.1.7", [1], ALL_CAPABILITIES)
    assert result.status is ConnectorCompatibilityStatus.INCOMPATIBLE
    assert result.reason == "connector_version_below_minimum"


def test_supported_version_below_recommended_has_update_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compatibility_module, "RECOMMENDED_CONNECTOR_VERSION", "0.1.8-beta.7")
    result = connector_compatibility("0.1.8-beta.5", [1], ALL_CAPABILITIES)
    assert result.status is ConnectorCompatibilityStatus.UPDATE_AVAILABLE
    assert result.reason == "connector_update_recommended"
