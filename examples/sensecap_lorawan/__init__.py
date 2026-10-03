"""SenseCAP models consuming transport-independent LoRaWAN events."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast, override

from lorawan_connection import (
    DeviceCollection,
    DeviceDescriptor,
    DeviceEvent,
    EventType,
    Unsubscribe,
    Uplink,
    notify,
    subscribe,
)

VENDOR_ID = 0x02E8
S2101_MODEL_ID = "fc455aa2-01cf-492b-9359-a5d8c9a0e1b3"


@dataclass(frozen=True, slots=True)
class S2101State:
    """Measurements are absent until observed, and merge independently."""

    temperature: float | None = None
    humidity: float | None = None


def decode_s2101(data: bytes) -> dict[str, float]:
    """Decode channel-one measurements using Seeed's seven-byte records.

    The two-byte trailer is retained in the wire format. Seeed's reference
    decoder does not validate it; this implementation makes no CRC guarantee.
    """
    if len(data) < 9 or (len(data) - 2) % 7:
        raise ValueError("Invalid SenseCAP frame length")
    result = {}
    for offset in range(0, len(data) - 2, 7):
        channel = data[offset]
        measurement = int.from_bytes(data[offset + 1 : offset + 3], "little")
        key = {4097: "temperature", 4098: "humidity"}.get(measurement)
        if channel != 1 or key is None:
            continue
        value = (
            int.from_bytes(data[offset + 3 : offset + 7], "little", signed=True) / 1000
        )
        if key == "humidity" and not 0 <= value <= 100:
            raise ValueError("Humidity outside valid range")
        if key == "temperature" and not -40 <= value <= 85:
            raise ValueError("Temperature outside S2101 measurement range")
        result[key] = value
    return result


class S2101:
    """S2101 model with typed state and state subscriptions."""

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        """Initialize an unobserved model."""
        self.descriptor = descriptor
        self.state = S2101State()
        self._listeners: list[Callable[[S2101State], None]] = []
        self._updated: dict[str, datetime] = {}
        self.closed = False

    def subscribe(self, callback: Callable[[S2101State], None]) -> Unsubscribe:
        """Listen for state changes; read state directly for initial values."""
        return subscribe(self._listeners, callback)

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
        state = replace(self.state, **values)
        if state != self.state:
            self.state = state
            notify(self._listeners, state)

    def close(self) -> None:
        """Stop emitting state when a device is removed."""
        self.closed = True
        self._listeners.clear()


DEVICE_MODELS: dict[tuple[int | None, str], type[S2101]] = {
    (VENDOR_ID, S2101_MODEL_ID): S2101,
}


class SenseCapDeviceCollection(DeviceCollection[S2101]):
    """A collection automatically admitting reviewed SenseCAP catalog models."""

    @override
    def _create_device(self, descriptor: DeviceDescriptor) -> S2101 | None:
        model_class = DEVICE_MODELS.get(
            (descriptor.vendor_id, descriptor.catalog_model_id)
        )
        return model_class(descriptor) if model_class is not None else None
