"""Per-entity Alexa device type overrides independent from Home Assistant domains."""

from __future__ import annotations

from typing import Literal

from .domain.models import Entity

AlexaDeviceType = Literal["switch", "light", "outlet", "gate"]
ALEXA_DEVICE_TYPES: tuple[AlexaDeviceType, ...] = ("switch", "light", "outlet", "gate")

_DEVICE_DOMAINS: dict[AlexaDeviceType, frozenset[str]] = {
    "switch": frozenset({"switch"}),
    "light": frozenset({"switch", "light"}),
    "outlet": frozenset({"switch"}),
    "gate": frozenset({"switch"}),
}

_DISPLAY_CATEGORIES: dict[AlexaDeviceType, str] = {
    "switch": "SWITCH",
    "light": "LIGHT",
    "outlet": "SMARTPLUG",
    # Gates use generic ModeController semantics instead of the GARAGE_DOOR
    # template so Alexa does not impose garage-door-specific behavior.
    "gate": "OTHER",
}


def allowed_alexa_device_types(entity: Entity) -> tuple[AlexaDeviceType, ...]:
    """Return explicit Alexa types that are safe for this HA entity domain."""
    return tuple(
        value for value in ALEXA_DEVICE_TYPES if entity.ha_domain in _DEVICE_DOMAINS[value]
    )


def validate_alexa_device_type(entity: Entity, value: str) -> AlexaDeviceType:
    """Validate a requested override against the immutable HA entity domain."""
    if value not in ALEXA_DEVICE_TYPES:
        raise ValueError("unknown Alexa device type")
    typed: AlexaDeviceType = value
    if entity.ha_domain not in _DEVICE_DOMAINS[typed]:
        raise ValueError("Alexa device type incompatible with HA domain")
    return typed


def effective_alexa_device_type(entity: Entity) -> AlexaDeviceType | None:
    """Return a valid configured override, otherwise automatic behavior."""
    configured = entity.alexa_device_type
    if not isinstance(configured, str):
        return None
    try:
        return validate_alexa_device_type(entity, configured)
    except ValueError:
        return None


def overridden_display_category(entity: Entity) -> str | None:
    """Return the display category supplied by an explicit override."""
    configured = effective_alexa_device_type(entity)
    return _DISPLAY_CATEGORIES.get(configured) if configured is not None else None


def is_gate_override(entity: Entity) -> bool:
    """Return whether this switch is intentionally exposed as an open/close gate."""
    return effective_alexa_device_type(entity) == "gate"
