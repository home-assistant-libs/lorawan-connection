"""Exercise device codec fixtures and invalid event handling."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from dragino_lorawan import LHT65
from lorawan_connection import Device, DeviceDescriptor, UplinkEvent
from milesight_lorawan import TS201, UC51x
from sensecap_lorawan import S2102


def descriptor(model: type[Device]) -> DeviceDescriptor:
    brand_id, model_id = model.identifiers["tts"]
    return DeviceDescriptor(
        network_id="network",
        dev_eui="0201010101010101",
        name="Codec fixture",
        application_id="application",
        profile_id="profile",
        stack="tts",
        brand_id=brand_id,
        model_id=model_id,
    )


@pytest.mark.parametrize(
    ("model", "port", "payload", "attribute", "expected"),
    [
        (S2102, 2, "010310000000000000", "illuminance", 0),
        (TS201, 85, "93673401640002", "temperature", 30.8),
        (TS201, 85, "0367baff", "temperature", -7),
        (UC51x, 85, "030100", "valve_1_open", False),
        (LHT65, 2, "cbeaff9c026d01ff387fff", "external_temperature", -2),
    ],
)
def test_partial_invalid_and_old_uplinks(
    model: type[Device], port: int, payload: str, attribute: str, expected: float | bool
) -> None:
    """Decode source fixtures without losing readings to bad or unrelated data."""
    device = model(descriptor(model))
    event = UplinkEvent(
        descriptor=device.descriptor,
        received_at=datetime.now(UTC),
        f_port=port,
        data=bytes.fromhex(payload),
    )
    device._receive_event(event)
    assert getattr(device, attribute) == expected
    device._receive_event(replace(event, data=b"\x01"))
    device._receive_event(replace(event, f_port=99, data=b"\x00" * 11))
    device._receive_event(
        replace(
            event,
            received_at=event.received_at - timedelta(seconds=1),
            data=b"\x00" * len(event.data),
        )
    )
    assert getattr(device, attribute) == expected


def test_sensor_fault_clears_temperature() -> None:
    """A TS201 fault makes the old measurement unknown."""
    device = TS201(descriptor(TS201))
    event = UplinkEvent(
        descriptor=device.descriptor,
        received_at=datetime.now(UTC),
        f_port=85,
        data=bytes.fromhex("03673401"),
    )
    device._receive_event(event)
    device._receive_event(replace(event, data=bytes.fromhex("b36700")))
    assert device.temperature is None


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (TS201, "20cec79afa6402bdff"),
        (UC51x, "20ce3fa109641700000000"),
        (LHT65, "cbea0105026d4101aa7fff"),
    ],
)
def test_history_does_not_become_current_state(
    model: type[TS201 | UC51x | LHT65], payload: str
) -> None:
    """Device history replies do not report current telemetry."""
    assert model.decode(bytes.fromhex(payload)) == {}


@pytest.mark.parametrize(
    ("model", "payload", "expected"),
    [
        (UC51x, "030101", {"valve_1_open": True}),
        (
            LHT65,
            "cbf60b0d0376010add7fff",
            {
                "temperature": 28.29,
                "humidity": 88.6,
                "external_temperature": 27.81,
                "battery_voltage": 3.062,
                "external_sensor_disconnected": False,
                "reported_battery_status": 3,
            },
        ),
    ],
)
def test_ttn_codec_example(
    model: type[UC51x | LHT65], payload: str, expected: dict[str, float | int | bool]
) -> None:
    """Published TTN examples cover models without physical-device captures."""
    assert model.decode(bytes.fromhex(payload)) == expected
