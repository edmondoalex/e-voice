from apps.cloud_api.app.voice_categories import infer_standard_category


def infer(
    name: str,
    *,
    domain: str = "sensor",
    device_class: str | None = None,
    unit: str | None = None,
) -> str | None:
    return infer_standard_category(
        domain=domain,
        device_class=device_class,
        unit=unit,
        names=(name,),
    )


def test_classifies_energy_and_power_sensors() -> None:
    assert infer("Produzione Inst. FV Totale EASAS", device_class="power", unit="W") == (
        "photovoltaic_power"
    )
    assert infer("Today Load Consumption", device_class="energy", unit="kWh") == (
        "consumed_energy_today"
    )
    assert infer("Today Energy Export", device_class="energy", unit="kWh") == (
        "exported_energy_today"
    )


def test_classifies_battery_and_thermal_temperatures() -> None:
    assert infer("Battery % EASAS", device_class="battery", unit="%") == "battery_level"
    assert infer("Temp Sanicube Alta", device_class="temperature", unit="°C") == (
        "acs_temperature"
    )
    assert infer("Temp Volano PDC Alta", device_class="temperature", unit="°C") == (
        "thermal_temperature"
    )


def test_classifies_security_entities() -> None:
    assert infer("Porta ufficio", domain="lock") == "lock_status"
    assert infer("Portone garage", domain="binary_sensor") == "opening_status"
    assert infer("Sistema allarme", domain="alarm_control_panel") == "alarm_status"


def test_leaves_ambiguous_entity_unassigned() -> None:
    assert infer("Sensore generico", device_class=None, unit=None) is None
