"""Identity validation and fixture payload ownership."""

from dataclasses import FrozenInstanceError, replace

import pytest

from lorawan_connection import (
    AckData,
    CoordinatesData,
    DeviceEventData,
    EventType,
    JoinData,
    LocationData,
    LogData,
    StatusData,
    TxAckData,
    UplinkData,
)

from .conftest import DESCRIPTOR, NOW


@pytest.mark.parametrize(
    "eui", ["", "xyz", "0" * 17, "g" * 16, "00-11-22-33-44-55-66-77"]
)
def test_invalid_dev_eui(eui: str) -> None:
    with pytest.raises(ValueError, match="eight hexadecimal bytes"):
        replace(DESCRIPTOR, dev_eui=eui)


def test_normalized_frozen_descriptor() -> None:
    descriptor = replace(DESCRIPTOR, dev_eui="AA:BB:CC:DD:EE:FF:00:11")
    assert descriptor.dev_eui == "aabbccddeeff0011"
    with pytest.raises(FrozenInstanceError):
        descriptor.name = "changed"  # type: ignore[misc]


def test_payload_borrowed_without_copying() -> None:
    payload = UplinkData(b"\x01", 10)
    event = DeviceEventData(
        "network", DESCRIPTOR.dev_eui, EventType.UPLINK, NOW, data=payload
    )
    assert event.data is payload
    with pytest.raises(FrozenInstanceError):
        event.data = None  # type: ignore[misc]


def test_fixture_defaults_and_nested_ownership() -> None:
    assert UplinkData(b"").f_port == 1
    assert StatusData().battery_level_unavailable
    assert (
        StatusData(battery_level=0, battery_level_unavailable=False).battery_level == 0
    )
    coordinates = CoordinatesData(52, 4, 3)
    assert LocationData(coordinates).location is coordinates
    assert JoinData("12345678").dev_addr == "12345678"
    assert AckData("queue", False).acknowledged is False
    assert TxAckData("gateway", 1).downlink_id == 1
    assert LogData("message", 1, 2).code == 2
