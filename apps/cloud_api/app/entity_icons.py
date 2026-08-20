"""Small local SVG icon set for safe server-rendered entity presentation."""

from __future__ import annotations

# SVG path data is intentionally indivisible.
# ruff: noqa: E501
MDI_PATHS = {
    "mdi:lightbulb": "M9 21h6v-1H9v1m3-19a7 7 0 0 0-4 12.74V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.26A7 7 0 0 0 12 2Z",
    "mdi:ceiling-light": "M8 3h8v2H8V3m4 3a7 7 0 0 1 7 7H5a7 7 0 0 1 7-7m-1 9h2v6h-2v-6Z",
    "mdi:toggle-switch": "M17 7H7a5 5 0 0 0 0 10h10a5 5 0 0 0 0-10m0 8a3 3 0 1 1 0-6 3 3 0 0 1 0 6Z",
    "mdi:blinds": "M5 3h14v2H5V3m1 3h12v13h-5v2h-2v-2H6V6m2 2v2h8V8H8m0 4v2h8v-2H8m0 4v1h8v-1H8Z",
    "mdi:thermostat": "M15 13V5a3 3 0 0 0-6 0v8a5 5 0 1 0 6 0m-3 7a3 3 0 0 1-1-5.83V5a1 1 0 0 1 2 0v9.17A3 3 0 0 1 12 20Z",
    "mdi:fan": "M12 11a1 1 0 1 0 0 2 1 1 0 0 0 0-2m.5-9c3 0 4 4 1.5 7.5C18 7 22 8 22 11c0 3-4 4-7.5 1.5C17 16 16 20 13 20c-3 0-4-4-1.5-7.5C8 15 4 14 4 11c0-3 4-4 7.5-1.5C9 6 9.5 2 12.5 2Z",
    "mdi:play-circle": "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20m-2 5.5 6 4.5-6 4.5v-9Z",
    "mdi:home-automation": "M12 3 2 12h3v8h6v-6h2v6h6v-8h3L12 3Z",
}

DOMAIN_ICONS = {
    "light": "mdi:lightbulb",
    "switch": "mdi:toggle-switch",
    "cover": "mdi:blinds",
    "climate": "mdi:thermostat",
    "fan": "mdi:fan",
    "scene": "mdi:play-circle",
}


def entity_icon_svg(icon: str | None, domain: str) -> str:
    """Render a known local MDI path, falling back safely by domain."""
    resolved = icon if icon in MDI_PATHS else DOMAIN_ICONS.get(domain, "mdi:home-automation")
    path = MDI_PATHS[resolved]
    return (
        f'<svg class="entity-icon" viewBox="0 0 24 24" aria-hidden="true" '
        f'data-icon="{resolved}" width="32" height="32"><path d="{path}"></path></svg>'
    )
