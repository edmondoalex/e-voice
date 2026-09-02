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
