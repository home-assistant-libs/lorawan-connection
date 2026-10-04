"""LT-22222-L input modes, output states, and payload validation."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from dragino_lorawan import LT22222
from lorawan_connection import AckData, DeviceEventData, EventType, UplinkData

from .conftest import DESCRIPTOR


def event(data: bytes, port: int = 2) -> DeviceEventData:
    return DeviceEventData(
        network_id="network",
        dev_eui=DESCRIPTOR.dev_eui,
        type=EventType.UPLINK,
        received_at=datetime.now(UTC),
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
    assert states == [{1: True, 2: False}] * 2
    model.handle_event(event(bytes(8) + bytes((0x40, 0, 0x40 | mode))))
    assert model.relays == {1: False, 2: True}
    model.close()
    model.handle_event(report)
    assert len(states) == 3


@pytest.mark.parametrize(
    "data,port",
    [
        (b"", 2),
        (bytes(10), 2),
        (bytes(12), 2),
        (bytes(10) + b"\x46", 2),
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


@pytest.mark.parametrize(
    "payload,voltages,currents,inputs,counts,voltage_count",
    [
        (
            "04ab04ac13101300890041",
            {1: 1.195, 2: 1.196},
            {1: 4.880, 2: 4.864},
            {1: True, 2: False},
            {1: None, 2: None},
            None,
        ),
        (
            "ffffffff80000001890042",
            {1: None, 2: None},
            {1: None, 2: None},
            {1: None, 2: None},
            {1: 4294967295, 2: 2147483649},
            None,
        ),
        (
            "8000000113101300890043",
            {1: None, 2: None},
            {1: 4.880, 2: 4.864},
            {1: None, 2: None},
            {1: 2147483649, 2: None},
            None,
        ),
        (
            "80000001ffffffff890044",
            {1: None, 2: None},
            {1: None, 2: None},
            {1: None, 2: None},
            {1: 2147483649, 2: None},
            4294967295,
        ),
        (
            "04ab04ac1310ffff890045",
            {1: 1.195, 2: 1.196},
            {1: 4.880, 2: None},
            {1: None, 2: None},
            {1: 65535, 2: None},
            None,
        ),
    ],
)
def test_input_modes(
    payload: str,
    voltages: dict[int, float | None],
    currents: dict[int, float | None],
    inputs: dict[int, bool | None],
    counts: dict[int, int | None],
    voltage_count: int | None,
) -> None:
    model = LT22222(DESCRIPTOR)
    listener = Mock()
    model.add_update_listener(listener)
    model.handle_event(event(bytes.fromhex(payload)))
    assert model.voltages == voltages
    assert model.currents == currents
    assert model.digital_inputs == inputs
    assert model.digital_counts == counts
    assert model.voltage_count == voltage_count
    assert model.relays == {1: True, 2: False}
    assert model.digital_outputs == {1: True, 2: False}
    assert model.mode == (bytes.fromhex(payload)[10] & 0x3F)
    listener.assert_called_once_with()


def test_mode_change_clears_inapplicable_values() -> None:
    model = LT22222(DESCRIPTOR)
    model.handle_event(event(bytes.fromhex("04ab04ac131013001f0041")))
    assert model.digital_inputs == {1: True, 2: True}
    model.handle_event(event(bytes.fromhex("0000000000000000200042")))
    assert model.digital_inputs == {1: None, 2: None}
    assert model.voltages == {1: None, 2: None}
    assert model.currents == {1: None, 2: None}
    assert model.digital_counts == {1: 0, 2: 0}
    model.handle_event(event(bytes.fromhex("0000000000000000000041")))
    assert model.digital_counts == {1: None, 2: None}
    assert model.voltages == {1: 0.0, 2: 0.0}
    assert model.digital_inputs == {1: False, 2: False}


def test_inputs_notify_without_output_change() -> None:
    model = LT22222(DESCRIPTOR)
    model.handle_event(event(bytes.fromhex("04ab04ac13101300890041")))
    listener = Mock()
    model.add_update_listener(listener)
    model.handle_event(event(bytes.fromhex("04ac04ac13101300910041")))
    assert model.voltages[1] == 1.196
    assert model.digital_inputs == {1: False, 2: True}
    listener.assert_called_once_with()
    model.handle_event(event(bytes.fromhex("ffff000000000000000046")))
    assert model.voltages[1] == 1.196
    assert model.digital_inputs == {1: False, 2: True}
    listener.assert_called_once_with()


@pytest.mark.parametrize("mode_byte", [0x01, 0x41])
def test_manual_mode_one(mode_byte: int) -> None:
    """The vendor MOD1 example decodes regardless of hardware label bits."""
    model = LT22222(DESCRIPTOR)
    model.handle_event(
        event(bytes.fromhex("04ab04ac13101300aaff") + bytes([mode_byte]))
    )
    assert model.voltages == {1: 1.195, 2: 1.196}
    assert model.digital_inputs == {1: True, 2: False}
    assert model.digital_outputs == {1: False, 2: True}
    assert model.relays == {1: True, 2: False}


def test_first_counter_report() -> None:
    model = LT22222(DESCRIPTOR)
    model.handle_event(event(bytes.fromhex("0000000100000002200042")))
    assert model.digital_counts == {1: 1, 2: 2}
