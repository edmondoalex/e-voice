"""Alexa exposure modes for Home Assistant cover entities."""

from __future__ import annotations

from typing import Literal

from .domain.models import Entity

AlexaCoverMode = Literal["discrete", "percentage", "hybrid"]

COVER_OPEN = 1
COVER_CLOSE = 2
COVER_SET_POSITION = 4
COVER_STOP = 8
COVER_MODES: tuple[AlexaCoverMode, ...] = ("discrete", "percentage", "hybrid")


def supports_discrete(entity: Entity) -> bool:
    """Return whether HA advertises both safe discrete movement operations."""
    required = COVER_OPEN | COVER_CLOSE
    return entity.supported_features & required == required


def supports_percentage(entity: Entity) -> bool:
    """Return whether HA advertises absolute cover positioning."""
    return bool(entity.supported_features & COVER_SET_POSITION)


def supports_stop(entity: Entity) -> bool:
    """Return whether HA advertises the typed stop_cover operation."""
    return bool(entity.supported_features & COVER_STOP)


def effective_cover_mode(entity: Entity) -> AlexaCoverMode | None:
    """Resolve a configured mode, or choose the safest feature-derived default."""
    discrete = supports_discrete(entity)
    percentage = supports_percentage(entity)
    configured = entity.alexa_cover_mode
    if configured == "discrete":
        return "discrete" if discrete else None
    if configured == "percentage":
        return "percentage" if percentage else None
    if configured == "hybrid":
        return "hybrid" if discrete and percentage else None
    if percentage:
        return "percentage"
    if discrete:
        return "discrete"
    return None


def validate_cover_mode(entity: Entity, mode: str) -> AlexaCoverMode:
    """Validate a requested mode against the synchronized HA feature flags."""
    if mode not in COVER_MODES:
        raise ValueError("unknown cover mode")
    typed_mode: AlexaCoverMode = mode
    if typed_mode == "discrete" and not supports_discrete(entity):
        raise ValueError("discrete mode requires open and close support")
    if typed_mode == "percentage" and not supports_percentage(entity):
        raise ValueError("percentage mode requires set-position support")
    if typed_mode == "hybrid" and not (supports_discrete(entity) and supports_percentage(entity)):
        raise ValueError("hybrid mode requires open, close and set-position support")
    return typed_mode
