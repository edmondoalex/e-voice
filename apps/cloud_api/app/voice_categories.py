"""Categorie vocali semantiche configurabili dal portale."""

import re

STANDARD_VOICE_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("photovoltaic_power", "Potenza fotovoltaica", "Produzione fotovoltaica istantanea"),
    ("consumption_power", "Consumo istantaneo", "Potenza consumata in questo momento"),
    ("grid_power", "Potenza di rete", "Potenza prelevata o immessa in rete"),
    ("produced_energy_today", "Energia prodotta oggi", "Energia prodotta nella giornata"),
    ("consumed_energy_today", "Energia consumata oggi", "Energia consumata nella giornata"),
    ("imported_energy_today", "Energia importata oggi", "Energia prelevata oggi dalla rete"),
    ("exported_energy_today", "Energia esportata oggi", "Energia immessa oggi in rete"),
    ("battery_level", "Livello batteria", "Percentuale di carica della batteria"),
    ("temperature", "Temperatura", "Temperatura ambiente o generica"),
    ("acs_temperature", "Temperatura acqua calda", "Temperatura ACS, boiler o bollitore"),
    (
        "thermal_temperature",
        "Temperatura centrale termica",
        "Temperatura di puffer, volano o accumulo termico",
    ),
    ("alarm_status", "Stato allarme", "Valore corrente del sistema di allarme"),
    ("lock_status", "Stato serratura", "Stato di una serratura"),
    ("opening_status", "Stato apertura", "Stato di porta, portone o altra apertura"),
)


def category_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")
    if not slug or len(slug) > 64:
        raise ValueError("invalid category slug")
    return slug


def infer_standard_category(
    *,
    domain: str,
    device_class: str | None,
    unit: str | None,
    names: tuple[str, ...],
) -> str | None:
    """Classifica in modo prudente un'entità non ancora assegnata."""
    text = " ".join(names).casefold()
    normalized = re.sub(r"[^a-z0-9àèéìòù%]+", " ", text)
    words = set(normalized.split())
    device_class = (device_class or "").casefold()
    unit = (unit or "").strip().casefold()

    if domain == "lock":
        return "lock_status"
    if domain == "alarm_control_panel":
        return "alarm_status"
    if domain == "binary_sensor" and words & {"porta", "portone", "portoncino", "garage"}:
        return "opening_status"
    if device_class == "battery" or "battery" in words or "batteria" in words:
        return "battery_level"
    if device_class == "temperature" or unit in {"°c", "°f"} or "temp" in words:
        if words & {"acs", "sanitario", "sanicube", "bollitore", "boiler"} or (
            "acqua" in words and "calda" in words
        ):
            return "acs_temperature"
        if words & {"puffer", "volano"} or ("accumulo" in words and "termico" in words):
            return "thermal_temperature"
        return "temperature"
    if device_class == "energy" or unit in {"wh", "kwh", "mwh"}:
        if words & {"export", "exported", "esportata", "immessa"}:
            return "exported_energy_today"
        if words & {"import", "imported", "importata", "prelevata"}:
            return "imported_energy_today"
        if words & {"production", "produced", "prodotta", "produzione"}:
            return "produced_energy_today"
        if words & {"consumption", "consumed", "consumata", "load"}:
            return "consumed_energy_today"
        return None
    if device_class == "power" or unit in {"w", "kw", "mw"}:
        if words & {"fotovoltaico", "fotovoltaica", "pv", "fv"}:
            return "photovoltaic_power"
        if words & {"consumo", "consumi", "load", "assorbimento"}:
            return "consumption_power"
        if words & {"rete", "grid", "pcc", "import", "export"}:
            return "grid_power"
    return None
