"""Dragino relay models consuming shared LoRaWAN events."""

from datetime import UTC, datetime, timedelta
from typing import ClassVar, cast, override

from lorawan_connection import (
    Device,
    DeviceCollection,
    DeviceDescriptor,
    DeviceEvent,
    EventType,
    Uplink,
)

VENDOR_ID = 676


class LT22222(Device):
    """Two relay outputs; commands leave the other channel unchanged."""

    vendor_id: ClassVar[int] = VENDOR_ID
    catalog_model_id: ClassVar[str] = "cb0a7bef-eaa0-4c61-a0b6-ce33e6ecbc4f"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        """Initialize both relay states as unobserved."""
        super().__init__(descriptor)
        self.relays: dict[int, bool | None] = {1: None, 2: None}

    @override
    def handle_event(self, event: DeviceEvent) -> None:
        if self.closed or event.type != EventType.UPLINK or event.data is None:
            return
        uplink = cast(Uplink, event.data)
        data = uplink.data
        if uplink.f_port != 2 or len(data) != 11:
            return
        # Trigger reports (mode 6) and LT-33222 frames have different semantics.
        if data[10] >> 6 != 1 or not 1 <= (data[10] & 0x3F) <= 5:
            return
        relays: dict[int, bool | None] = {
            1: bool(data[8] & 0x80),
            2: bool(data[8] & 0x40),
        }
        if relays != self.relays:
            self.relays = relays
            self.notify()

    async def async_set_relay(self, channel: int, on: bool) -> str:
        """Await command acknowledgement; telemetry updates reported state."""
        if channel not in (1, 2):
            raise ValueError("Relay channel must be 1 or 2")
        states = [0x11, 0x11]
        states[channel - 1] = int(on)
        return await self.async_send_downlink(
            data=bytes((0x03, *states)),
            f_port=2,
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )


class DraginoDevices(DeviceCollection[LT22222]):
    """Supported Dragino catalog models."""

    DEVICES = (LT22222,)
