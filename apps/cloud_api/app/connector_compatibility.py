"""Cloud policy for Home Assistant Connector compatibility."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

MINIMUM_SUPPORTED_CONNECTOR_VERSION = "0.1.8-beta.5"
RECOMMENDED_CONNECTOR_VERSION = "0.1.8-beta.5"
REQUIRED_EVCP_PROTOCOL_VERSION = 1
REQUIRED_CONNECTOR_CAPABILITIES = frozenset(
    {
        "supports_correlation_id",
        "supports_command_diagnostics",
        "supports_heartbeat_diagnostics",
    }
)

_VERSION = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<label>[0-9A-Za-z-]+)(?:\.(?P<number>\d+))?)?$"
)


class ConnectorCompatibilityStatus(StrEnum):
    OK = "OK"
    UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNKNOWN_OFFLINE = "UNKNOWN/OFFLINE"


@dataclass(frozen=True, slots=True)
class ConnectorCompatibility:
    status: ConnectorCompatibilityStatus
    reason: str
    selected_protocol: int | None
    missing_capabilities: tuple[str, ...] = ()


def _version_key(value: str) -> tuple[int, int, int, int, str, int] | None:
    match = _VERSION.fullmatch(value.strip().removeprefix("v"))
    if match is None:
        return None
    label = match.group("label")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        1 if label is None else 0,
        label or "",
        int(match.group("number") or 0),
    )


def effective_connector_capabilities(
    connector_version: str, declared: dict[str, bool]
) -> dict[str, bool]:
    """Use declarations when present, with a bounded fallback for known released code."""
    if declared:
        return declared
    current = _version_key(connector_version)
    minimum = _version_key(MINIMUM_SUPPORTED_CONNECTOR_VERSION)
    if current is not None and minimum is not None and current >= minimum:
        return {name: True for name in REQUIRED_CONNECTOR_CAPABILITIES}
    return {}


def connector_compatibility(
    connector_version: str | None,
    protocol_versions: list[int] | tuple[int, ...] | None,
    capabilities: dict[str, bool] | None,
) -> ConnectorCompatibility:
    """Evaluate the declared connector contract against cloud requirements."""
    if connector_version is None or protocol_versions is None or capabilities is None:
        return ConnectorCompatibility(
            ConnectorCompatibilityStatus.UNKNOWN_OFFLINE, "connector_metadata_missing", None
        )
    selected_protocol = (
        REQUIRED_EVCP_PROTOCOL_VERSION
        if REQUIRED_EVCP_PROTOCOL_VERSION in protocol_versions
        else None
    )
    if selected_protocol is None:
        return ConnectorCompatibility(
            ConnectorCompatibilityStatus.INCOMPATIBLE,
            "required_evcp_protocol_not_supported",
            None,
        )
    missing = tuple(
        sorted(
            name for name in REQUIRED_CONNECTOR_CAPABILITIES if capabilities.get(name) is not True
        )
    )
    if missing:
        return ConnectorCompatibility(
            ConnectorCompatibilityStatus.INCOMPATIBLE,
            "required_connector_capabilities_missing",
            selected_protocol,
            missing,
        )
    current = _version_key(connector_version)
    minimum = _version_key(MINIMUM_SUPPORTED_CONNECTOR_VERSION)
    recommended = _version_key(RECOMMENDED_CONNECTOR_VERSION)
    if current is None or minimum is None or recommended is None:
        return ConnectorCompatibility(
            ConnectorCompatibilityStatus.INCOMPATIBLE,
            "connector_version_invalid",
            selected_protocol,
        )
    if current < minimum:
        return ConnectorCompatibility(
            ConnectorCompatibilityStatus.INCOMPATIBLE,
            "connector_version_below_minimum",
            selected_protocol,
        )
    if current < recommended:
        return ConnectorCompatibility(
            ConnectorCompatibilityStatus.UPDATE_AVAILABLE,
            "connector_update_recommended",
            selected_protocol,
        )
    return ConnectorCompatibility(ConnectorCompatibilityStatus.OK, "compatible", selected_protocol)
