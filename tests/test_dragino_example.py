"""Two independent relay states and complete payload validation."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from dragino_lorawan import LT22222
from lorawan_connection import AckData, DeviceEventData, EventType, UplinkData

from .conftest import DESCRIPTOR


def event(data: bytes, port: int = 2) -> DeviceEventData:
    return DeviceEventData(
        "network",
        DESCRIPTOR.dev_eui,
        EventType.UPLINK,
        datetime.now(UTC),
        data=UplinkData(data, port),
    )


@pytest.mark.parametrize("mode", range(1, 6))
def test_relay_reports(mode: int) -> None:
    model = LT22222(DESCRIPTOR)
    states = []
    model.add_update_listener(lambda: states.append(model.relays.copy()))
    report = event(bytes(8) + bytes((0x80, 0, 0x40 | mode)))
    model.handle_event(report)
    model.handle_event(report)
    assert states == [{1: True, 2: False}]
    model.handle_event(event(bytes(8) + bytes((0x40, 0, 0x40 | mode))))
    assert model.relays == {1: False, 2: True}
    model.close()
    model.handle_event(report)
    assert len(states) == 2


@pytest.mark.parametrize(
    "data,port",
    [
        (b"", 2),
        (bytes(10), 2),
        (bytes(12), 2),
        (bytes(10) + b"\x46", 2),
        (bytes(10) + b"\x01", 2),
        (bytes(10) + b"\x41", 3),
    ],
)
def test_unsupported_data(data: bytes, port: int) -> None:
    model = LT22222(DESCRIPTOR)
    listener = Mock()
    model.add_update_listener(listener)
    model.handle_event(event(data, port))
    assert model.relays == {1: None, 2: None}
    listener.assert_not_called()


def test_ack_does_not_set_state() -> None:
    model = LT22222(DESCRIPTOR)
    model.handle_event(
        replace(event(b""), type=EventType.ACK, data=AckData("queue-id", True))
    )
    assert model.relays == {1: None, 2: None}
