"""SenseCAP models consuming transport-independent LoRaWAN events."""

import logging
from datetime import datetime
from typing import override

from lorawan_connection import (
    Device,
    DeviceCollection,
    DeviceDescriptor,
    DeviceEvent,
    EventType,
)

_LOGGER = logging.getLogger(__name__)

VENDOR_ID = 0x02E8


def decode_s2101(data: bytes) -> dict[str, float | None]:
    """Decode channel-one measurements using Seeed's seven-byte records.

    The two-byte trailer is retained in the wire format. Seeed's reference
    decoder does not validate it; this implementation makes no CRC guarantee.
    """
    if len(data) < 9 or (len(data) - 2) % 7:
        raise ValueError("Invalid SenseCAP frame length")
    result: dict[str, float | None] = {}
    for offset in range(0, len(data) - 2, 7):
        channel = data[offset]
        measurement = int.from_bytes(data[offset + 1 : offset + 3], "little")
        key = {4097: "temperature", 4098: "humidity"}.get(measurement)
        if channel != 1 or key is None:
            continue
        value = (
            int.from_bytes(data[offset + 3 : offset + 7], "little", signed=True) / 1000
        )
        if value >= 2_000_000:
            _LOGGER.warning("SenseCAP %s sensor fault: %s", key, value)
            result[key] = None
            continue
        if key == "humidity" and not 0 <= value <= 100:
            raise ValueError("Humidity outside valid range")
        if key == "temperature" and not -40 <= value <= 85:
            raise ValueError("Temperature outside S2101 measurement range")
        result[key] = value
    return result


class S2101(Device):
    """S2101 measurements and update notifications."""

    identifiers = {
        "chirpstack": (VENDOR_ID, "fc455aa2-01cf-492b-9359-a5d8c9a0e1b3"),
        "tts": ("sensecap", "sensecaps2101-temp-humid"),
    }

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        """Initialize an unobserved model."""
        super().__init__(descriptor)
        self.temperature: float | None = None
        self.humidity: float | None = None
        self._updated: dict[str, datetime] = {}

    @override
    def handle_event(self, event: DeviceEvent) -> None:
        """Merge a valid partial measurement without clearing other values."""
        if self.closed or event.type != EventType.UPLINK:
            return
        if event.f_port not in (1, 2):
            return
        try:
            values = decode_s2101(event.data)
        except ValueError:
            return
        values = {
            key: value
            for key, value in values.items()
            if key not in self._updated or event.received_at >= self._updated[key]
        }
        if not values:
            return
        self._updated.update(dict.fromkeys(values, event.received_at))
        for key, value in values.items():
            setattr(self, key, value)
        self.notify()


class S2102(Device):
    """SenseCAP light intensity in lux."""

    identifiers = {
        "chirpstack": (VENDOR_ID, "92e11305-8190-41cc-84d0-ddc452cf1889"),
        "tts": ("sensecap", "sensecaps2102-light"),
    }

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        super().__init__(descriptor)
        self.illuminance: float | None = None
        self._updated_at: datetime | None = None

    @override
    def handle_event(self, event: DeviceEvent) -> None:
        if self.closed or event.type != EventType.UPLINK or event.f_port not in (1, 2):
            return
        if self._updated_at is not None and event.received_at < self._updated_at:
            return
        data = event.data
        if len(data) < 9 or (len(data) - 2) % 7:
            return
        observed = False
        value: float | None = None
        for offset in range(0, len(data) - 2, 7):
            if (
                data[offset] != 1
                or int.from_bytes(data[offset + 1 : offset + 3], "little") != 4099
            ):
                continue
            reading = (
                int.from_bytes(data[offset + 3 : offset + 7], "little", signed=True)
                / 1000
            )
            if reading >= 2_000_000:
                value = None
            elif 0 <= reading <= 160_000:
                value = reading
            else:
                return
            observed = True
        if observed:
            self.illuminance = value
            self._updated_at = event.received_at
            self.notify()


type SenseCapDevice = S2101 | S2102


class SenseCapDeviceCollection(DeviceCollection[SenseCapDevice]):
    """Supported SenseCAP catalog models."""

    DEVICES = (S2101, S2102)
