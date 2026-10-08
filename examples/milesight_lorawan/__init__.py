"""Milesight models following the TTN TS201 and UC51x codecs."""

from collections.abc import Iterator
from datetime import datetime
from typing import override

from lorawan_connection import (
    Device,
    DeviceCollection,
    DeviceDescriptor,
    DeviceEvent,
    EventType,
)


class MilesightDevice(Device):
    """Merge current readings and keep MAC status independent of uplink battery."""

    model_name: str

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        super().__init__(descriptor)
        self.reported_battery_percent: int | None = None
        self._updated: dict[str, datetime] = {}

    @staticmethod
    def decode(data: bytes) -> dict[str, float | int | bool | None]:
        raise NotImplementedError

    @override
    def handle_event(self, event: DeviceEvent) -> None:
        if self.closed or event.type != EventType.UPLINK or event.f_port != 85:
            return
        try:
            values = self.decode(event.data)
        except ValueError:
            return
        values = {
            name: value
            for name, value in values.items()
            if name not in self._updated or event.received_at >= self._updated[name]
        }
        if values:
            for name, value in values.items():
                setattr(self, name, value)
            self._updated.update(dict.fromkeys(values, event.received_at))
            self.notify()


ATTRIBUTE_LENGTHS = {
    (0xFF, 0x01): 1,
    (0xFF, 0x09): 2,
    (0xFF, 0x0A): 2,
    (0xFF, 0xFF): 2,
    (0xFF, 0x16): 8,
    (0xFF, 0x0F): 1,
    (0xFF, 0xFE): 1,
    (0xFF, 0x0B): 1,
}


def records(
    data: bytes, lengths: dict[tuple[int, int], int]
) -> Iterator[tuple[tuple[int, int], bytes]]:
    offset = 0
    while offset < len(data):
        if offset + 2 > len(data):
            raise ValueError("Truncated channel header")
        kind = data[offset], data[offset + 1]
        offset += 2
        length = lengths.get(kind, ATTRIBUTE_LENGTHS.get(kind))
        if length is None:
            # Unknown records have no length field; do not guess where the next starts.
            return
        if offset + length > len(data):
            raise ValueError("Truncated channel data")
        yield kind, data[offset : offset + length]
        offset += length


class TS201(MilesightDevice):
    """Temperature and alarm reports from the TS201."""

    identifiers = {"tts": ("milesight-iot", "ts201")}
    model_name = "TS201"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        super().__init__(descriptor)
        self.temperature: float | None = None
        self.temperature_error: bool | None = None
        self.temperature_alarm: int | None = None
        self.temperature_mutation: float | None = None

    @staticmethod
    @override
    def decode(data: bytes) -> dict[str, float | int | bool | None]:
        result: dict[str, float | int | bool | None] = {}
        lengths = {
            (0x01, 0x75): 1,
            (0x03, 0x67): 2,
            (0x83, 0x67): 3,
            (0x93, 0x67): 5,
            (0xB3, 0x67): 1,
            (0xFF, 0xA0): 9,
            (0x20, 0xCE): 7,
        }
        for kind, value in records(data, lengths):
            if kind == (0x01, 0x75):
                if value[0] > 100:
                    raise ValueError("Invalid battery percentage")
                result["reported_battery_percent"] = value[0]
            elif kind in ((0x03, 0x67), (0x83, 0x67), (0x93, 0x67)):
                result["temperature"] = (
                    int.from_bytes(value[:2], "little", signed=True) / 10
                )
                result["temperature_error"] = False
                if len(value) > 2:
                    result["temperature_alarm"] = value[-1]
                if len(value) == 5:
                    result["temperature_mutation"] = (
                        int.from_bytes(value[2:4], "little", signed=True) / 10
                    )
            elif kind == (0xB3, 0x67):
                result["temperature"] = None
                result["temperature_error"] = True
            # History records are not current measurements.
        return result


class UC51x(MilesightDevice):
    """Reported valve states and measurements from UC511 and UC512."""

    identifiers = {"tts": ("milesight-iot", "uc51x")}
    model_name = "UC51x"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        super().__init__(descriptor)
        self.valve_1_open: bool | None = None
        self.valve_2_open: bool | None = None
        self.valve_1_pulse_count: int | None = None
        self.valve_2_pulse_count: int | None = None
        self.valve_1_control_failed: bool | None = None
        self.valve_2_control_failed: bool | None = None
        self.gpio_1: bool | None = None
        self.gpio_2: bool | None = None
        self.pressure_kpa: float | None = None

    @staticmethod
    @override
    def decode(data: bytes) -> dict[str, float | int | bool | None]:
        result: dict[str, float | int | bool | None] = {}
        lengths = {
            (0x01, 0x75): 1,
            (0x03, 0x01): 1,
            (0x05, 0x01): 1,
            (0x04, 0xC8): 4,
            (0x06, 0xC8): 4,
            (0x07, 0x01): 1,
            (0x08, 0x01): 1,
            (0x09, 0x7B): 2,
            (0xB9, 0x7B): 1,
            (0x20, 0xCE): 9,
            (0x21, 0xCE): 6,
        }
        for kind, value in records(data, lengths):
            if kind == (0x01, 0x75):
                if value[0] > 100:
                    raise ValueError("Invalid battery percentage")
                result["reported_battery_percent"] = value[0]
            elif kind in ((0x03, 0x01), (0x05, 0x01)):
                index = 1 if kind[0] == 3 else 2
                if value[0] in (0, 1):
                    result[f"valve_{index}_open"] = bool(value[0])
                elif value[0] == 255:
                    result[f"valve_{index}_control_failed"] = True
            elif kind in ((0x04, 0xC8), (0x06, 0xC8)):
                index = 1 if kind[0] == 4 else 2
                result[f"valve_{index}_pulse_count"] = int.from_bytes(value, "little")
            elif kind in ((0x07, 0x01), (0x08, 0x01)) and value[0] in (0, 1):
                result[f"gpio_{kind[0] - 6}"] = bool(value[0])
            elif kind == (0x09, 0x7B):
                result["pressure_kpa"] = int.from_bytes(value, "little")
            elif kind == (0xB9, 0x7B):
                result["pressure_kpa"] = None
        return result


class MilesightDevices(DeviceCollection[MilesightDevice]):
    """Models with reviewed catalog identities."""

    DEVICES = (TS201, UC51x)
