"""SenseCAP models consuming transport-independent LoRaWAN events."""

import logging
from datetime import datetime
from typing import ClassVar, cast, override

from lorawan_connection import (
    Device,
    DeviceCollection,
    DeviceDescriptor,
    DeviceEvent,
    EventType,
    Uplink,
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

    vendor_id: ClassVar[int] = VENDOR_ID
    catalog_model_id: ClassVar[str] = "fc455aa2-01cf-492b-9359-a5d8c9a0e1b3"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        """Initialize an unobserved model."""
        super().__init__(descriptor)
        self.temperature: float | None = None
        self.humidity: float | None = None
        self._updated: dict[str, datetime] = {}

    @override
    def handle_event(self, event: DeviceEvent) -> None:
        """Merge a valid partial measurement without clearing other values."""
        if self.closed or event.type != EventType.UPLINK or event.data is None:
            return
        uplink = cast(Uplink, event.data)
        if uplink.f_port != 1:
            return
        try:
            values = decode_s2101(uplink.data)
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


class SenseCapDeviceCollection(DeviceCollection[S2101]):
    """A collection automatically admitting reviewed SenseCAP catalog models."""

    DEVICES = (S2101,)
