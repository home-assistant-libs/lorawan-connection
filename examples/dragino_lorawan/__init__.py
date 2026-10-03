"""Dragino device models consuming shared LoRaWAN events."""

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
    """Model for the Dragino LT-22222-L."""

    vendor_id: ClassVar[int] = VENDOR_ID
    catalog_model_id: ClassVar[str] = "cb0a7bef-eaa0-4c61-a0b6-ce33e6ecbc4f"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        """Initialize values as unobserved until a device report arrives."""
        super().__init__(descriptor)
        self.mode: int | None = None
        self.relays: dict[int, bool | None] = {1: None, 2: None}
        self.digital_outputs: dict[int, bool | None] = {1: None, 2: None}
        self.digital_inputs: dict[int, bool | None] = {1: None, 2: None}
        self.voltages: dict[int, float | None] = {1: None, 2: None}
        self.currents: dict[int, float | None] = {1: None, 2: None}
        self.digital_counts: dict[int, int | None] = {1: None, 2: None}
        self.voltage_count: int | None = None

    @override
    def handle_event(self, event: DeviceEvent) -> None:
        if self.closed or event.type != EventType.UPLINK or event.data is None:
            return
        uplink = cast(Uplink, event.data)
        data = uplink.data
        if uplink.f_port != 2 or len(data) != 11:
            return
        # Trigger reports contain flags, not input measurements or output states.
        if data[10] >> 6 != 1 or not 1 <= (data[10] & 0x3F) <= 5:
            return
        mode = data[10] & 0x3F
        relays: dict[int, bool | None] = {
            1: bool(data[8] & 0x80),
            2: bool(data[8] & 0x40),
        }
        digital_outputs: dict[int, bool | None] = {
            1: bool(data[8] & 1),
            2: bool(data[8] & 2),
        }
        digital_inputs: dict[int, bool | None] = {1: None, 2: None}
        voltages: dict[int, float | None] = {1: None, 2: None}
        currents: dict[int, float | None] = {1: None, 2: None}
        digital_counts: dict[int, int | None] = {1: None, 2: None}
        voltage_count = None

        if mode in (1, 5):
            voltages = {
                1: int.from_bytes(data[0:2], signed=True) / 1000,
                2: int.from_bytes(data[2:4], signed=True) / 1000,
            }
        if mode in (1, 3, 5):
            currents[1] = int.from_bytes(data[4:6], signed=True) / 1000
        if mode in (1, 3):
            currents[2] = int.from_bytes(data[6:8], signed=True) / 1000
        if mode == 1:
            digital_inputs = {1: bool(data[8] & 8), 2: bool(data[8] & 16)}
        if mode in (2, 3, 4):
            digital_counts[1] = int.from_bytes(data[0:4])
        if mode == 2:
            digital_counts[2] = int.from_bytes(data[4:8])
        if mode == 4:
            voltage_count = int.from_bytes(data[4:8])
        if mode == 5:
            digital_counts[1] = int.from_bytes(data[6:8])

        state = (
            mode,
            relays,
            digital_outputs,
            digital_inputs,
            voltages,
            currents,
            digital_counts,
            voltage_count,
        )
        if state != (
            self.mode,
            self.relays,
            self.digital_outputs,
            self.digital_inputs,
            self.voltages,
            self.currents,
            self.digital_counts,
            self.voltage_count,
        ):
            (
                self.mode,
                self.relays,
                self.digital_outputs,
                self.digital_inputs,
                self.voltages,
                self.currents,
                self.digital_counts,
                self.voltage_count,
            ) = state
            self.notify()

    async def async_set_relay(self, channel: int, on: bool) -> None:
        """Await command acknowledgement; telemetry updates reported state."""
        if channel not in (1, 2):
            raise ValueError("Relay channel must be 1 or 2")
        states = [0x11, 0x11]
        states[channel - 1] = int(on)
        await self.async_send_downlink(
            data=bytes((0x03, *states)),
            f_port=2,
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )

    async def async_set_digital_output(self, channel: int, on: bool) -> None:
        """Enable an active-low output and await its command acknowledgement."""
        if channel not in (1, 2):
            raise ValueError("Digital output channel must be 1 or 2")
        # The protocol includes DO3 even though this model has no third output.
        states = [0x11, 0x11, 0x11]
        states[channel - 1] = int(on)
        await self.async_send_downlink(
            data=bytes((0x02, *states)),
            f_port=2,
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )


class DraginoDevices(DeviceCollection[LT22222]):
    """Supported Dragino catalog models."""

    DEVICES = (LT22222,)
