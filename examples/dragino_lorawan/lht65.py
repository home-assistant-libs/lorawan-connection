"""Dragino LHT65 telemetry, following the TTN lht65 codec."""

from datetime import datetime
from typing import override

from lorawan_connection import Device, DeviceDescriptor, DeviceEvent, EventType


class LHT65(Device):
    """Decode current readings without interpreting datalog replies as live state."""

    identifiers = {"tts": ("dragino", "lht65")}
    model_name = "LHT65"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        super().__init__(descriptor)
        self.temperature: float | None = None
        self.humidity: float | None = None
        self.external_temperature: float | None = None
        self.battery_voltage: float | None = None
        self.external_voltage: float | None = None
        self.illuminance: float | None = None
        self.external_count: int | None = None
        self.external_input: bool | None = None
        self.external_interrupt: bool | None = None
        self.external_sensor_disconnected: bool | None = None
        self.reported_battery_status: int | None = None
        self._updated: dict[str, datetime] = {}

    @override
    def handle_event(self, event: DeviceEvent) -> None:
        if self.closed or event.type != EventType.UPLINK or event.f_port != 2:
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

    @staticmethod
    def decode(data: bytes) -> dict[str, float | int | bool | None]:
        if len(data) < 7:
            raise ValueError("Truncated LHT65 data")
        if data[6] & 0x40:
            return {}  # Datalog response, not a current reading.
        if len(data) != 11:
            raise ValueError("Invalid LHT65 frame length")
        extension = data[6] & 0x0F
        if extension == 15:
            return {}  # External probe identifier frame.
        humidity = (int.from_bytes(data[4:6], "big") & 0xFFF) / 10
        if not 0 <= humidity <= 100:
            raise ValueError("Humidity outside sensor range")
        result: dict[str, float | int | bool | None] = {
            "temperature": int.from_bytes(data[2:4], "big", signed=True) / 100,
            "humidity": humidity,
            "external_sensor_disconnected": bool(data[6] & 0x80),
        }
        if extension == 9:
            result["external_temperature"] = (
                int.from_bytes(data[:2], "big", signed=True) / 100
            )
            result["reported_battery_status"] = data[4] >> 6
        else:
            result["battery_voltage"] = (
                int.from_bytes(data[:2], "big") & 0x3FFF
            ) / 1000
            result["reported_battery_status"] = data[0] >> 6
        if extension == 1:
            raw = int.from_bytes(data[7:9], "big", signed=True)
            result["external_temperature"] = (
                None if data[6] & 0x80 or raw == 0x7FFF else raw / 100
            )
        elif extension == 4:
            result.update(
                external_input=bool(data[7]), external_interrupt=bool(data[8])
            )
        elif extension == 5:
            result["illuminance"] = int.from_bytes(data[7:9], "big")
        elif extension == 6:
            result["external_voltage"] = int.from_bytes(data[7:9], "big") / 1000
        elif extension in (7, 8):
            result["external_count"] = int.from_bytes(
                data[7 : 9 if extension == 7 else 11], "big"
            )
        return result
