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
