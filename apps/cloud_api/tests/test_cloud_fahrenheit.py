from types import SimpleNamespace

from apps.cloud_api.app.alexa import _climate_temperature_scale


def test_fahrenheit_degree_symbol_is_recognized():
    assert (
        _climate_temperature_scale(SimpleNamespace(attributes_json={"temperature_unit": "\u00b0F"}))
        == "FAHRENHEIT"
    )
