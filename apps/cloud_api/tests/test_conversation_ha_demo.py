from scripts.demo_conversation_ha import snapshots_from_states


def test_rest_states_are_reduced_to_safe_sensor_snapshots() -> None:
    snapshots = snapshots_from_states(
        [
            {
                "entity_id": "sensor.pv_power",
                "state": "3800",
                "last_updated": "2026-09-01T08:00:00Z",
                "attributes": {
                    "friendly_name": "Produzione fotovoltaico",
                    "device_class": "power",
                    "unit_of_measurement": "W",
                    "secret_attribute": "must not survive",
                },
            },
            {"entity_id": "switch.private", "state": "on", "attributes": {}},
        ]
    )

    assert len(snapshots) == 1
    assert snapshots[0].entity_id == "sensor.pv_power"
    assert snapshots[0].state == "3800"
    assert snapshots[0].unit == "W"
    assert not hasattr(snapshots[0], "secret_attribute")


def test_allowlist_excludes_unmapped_sensors_and_applies_friendly_names() -> None:
    states = [
        {
            "entity_id": "sensor.allowed",
            "state": "2000",
            "attributes": {"friendly_name": "Technical allowed", "device_class": "power"},
        },
        {
            "entity_id": "sensor.not_allowed",
            "state": "9000",
            "attributes": {"friendly_name": "Technical private", "device_class": "power"},
        },
    ]
    mappings = {
        "sensor.allowed": {
            "name": "fotovoltaico privato",
            "aliases": ["pannelli privato"],
        }
    }

    snapshots = snapshots_from_states(states, mappings)

    assert len(snapshots) == 1
    assert snapshots[0].entity_id == "sensor.allowed"
    assert snapshots[0].name == "fotovoltaico privato"
    assert snapshots[0].aliases == ("pannelli privato",)
