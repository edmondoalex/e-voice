"""Deterministic, read-only summary of authorized home state snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .conversation import EntitySnapshot


@dataclass(frozen=True, slots=True)
class HouseSummary:
    speech: str
    entities: tuple[EntitySnapshot, ...]


def _number(entity: EntitySnapshot) -> float | None:
    try:
        return float(entity.state) if entity.state is not None else None
    except (TypeError, ValueError):
        return None


def _watts(entity: EntitySnapshot) -> float | None:
    value = _number(entity)
    if value is None:
        return None
    return value * 1000 if (entity.unit or "").casefold() == "kw" else value


def summarize_house(entities: tuple[EntitySnapshot, ...]) -> HouseSummary:
    """Return at most three prioritized findings, never inferred device values."""
    findings: list[tuple[int, str, EntitySnapshot]] = []
    for entity in entities:
        state = (entity.state or "").casefold()
        label = entity.name
        if not entity.available or state in {"unknown", "unavailable", ""}:
            findings.append((10, f"{label} non è disponibile", entity))
            continue
        category = (entity.category or "").casefold()
        if (
            entity.domain == "binary_sensor"
            and category == "opening_status"
            and state
            in {
                "on",
                "open",
            }
        ):
            findings.append((20, f"{label} risulta aperta", entity))
        elif entity.domain == "lock" and state in {"unlocked", "open"}:
            findings.append((30, f"{label} risulta sbloccata", entity))
        elif entity.device_class == "battery" or category == "battery_level":
            value = _number(entity)
            if value is not None and value < 20:
                findings.append((40, f"{label} è al {round(value)} per cento", entity))
        elif entity.device_class == "temperature" or category in {
            "temperature",
            "temperature_ambiente",
            "thermal_temperature",
        }:
            value = _number(entity)
            if value is not None and (value < 10 or value > 30):
                findings.append((50, f"{label} misura {value:g} gradi", entity))
        elif category == "consumption_power":
            watts = _watts(entity)
            if watts is not None and watts > 5000:
                findings.append((60, f"{label} sta assorbendo {watts / 1000:g} kilowatt", entity))

    if not findings:
        return HouseSummary(
            "La casa è in ordine. Non risultano anomalie importanti tra i dispositivi autorizzati.",
            (),
        )
    findings.sort(key=lambda item: (item[0], item[2].name.casefold()))
    selected = findings[:3]
    speech = "Da controllare: " + "; ".join(item[1] for item in selected) + "."
    remaining = len(findings) - len(selected)
    if remaining:
        speech += f" Ci sono anche {remaining} altre segnalazioni."
    return HouseSummary(speech, tuple(item[2] for item in selected))
