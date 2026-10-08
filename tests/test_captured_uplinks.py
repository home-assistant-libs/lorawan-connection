"""Replay captured physical-device uplinks through the vendor libraries."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from lorawan_connection import Device, DeviceCollection, DeviceDescriptor, UplinkEvent
from lorawan_connection.mock import MockConnection
from milesight_lorawan import TS201, MilesightDevices
from sensecap_lorawan import S2101, S2102, SenseCapDeviceCollection


@pytest.mark.parametrize(
    ("model", "collection_type", "fixture_name"),
    [
        pytest.param(S2101, SenseCapDeviceCollection, "s2101", id="physical-s2101"),
        pytest.param(S2102, SenseCapDeviceCollection, "s2102", id="physical-s2102"),
        pytest.param(TS201, MilesightDevices, "ts201", id="physical-ts201"),
    ],
)
async def test_captured_uplink(
    model: type[Device], collection_type: type[DeviceCollection], fixture_name: str
) -> None:
    """An unmodified capture selects its official model and updates observers."""
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/device_uplinks" / f"{fixture_name}.json"
        ).read_text()
    )
    brand_id, model_id = model.identifiers["tts"]
    descriptor = DeviceDescriptor(
        network_id="network",
        dev_eui="0201010101010101",
        name="Captured sensor",
        application_id="application",
        profile_id="profile",
        stack="tts",
        brand_id=brand_id,
        model_id=model_id,
    )
    connection = MockConnection([descriptor])
    collection = collection_type(connection)
    await collection.async_setup()
    try:
        device = collection.devices[descriptor.dev_eui]
        assert isinstance(device, model)
        assert model.__name__ == fixture["model"]
        listener = Mock()
        device.add_update_listener(listener)
        connection.emit(
            UplinkEvent(
                descriptor=descriptor,
                received_at=datetime(2026, 10, 8, tzinfo=UTC),
                f_port=fixture["f_port"],
                data=bytes.fromhex(fixture["payload"]),
            )
        )
        assert {key: getattr(device, key) for key in fixture["expected"]} == fixture[
            "expected"
        ]
        listener.assert_called_once_with()
        assert device.battery_level is None
    finally:
        collection.close()
