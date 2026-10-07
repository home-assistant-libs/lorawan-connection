"""Device identity and synchronous update notifications."""

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import ClassVar

from .callbacks import Unsubscribe, notify, subscribe
from .downlink import Downlink, DownlinkError, SendDownlink
from .events import AckEvent, DeviceDescriptor, DeviceEvent, StatusEvent


class Device:
    """Own identity and listeners; subclasses define data and interpret events."""

    identifiers: ClassVar[Mapping[str, tuple[int | str, str]]] = {}

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        self.descriptor = descriptor
        self._listeners: list[Callable[[None], None]] = []
        self._remove_listeners: list[Callable[[None], None]] = []
        self._closed = False
        self._send_downlink: SendDownlink | None = None
        self._pending_acks: dict[str, asyncio.Future[bool]] = {}
        self._early_acks: dict[str, bool] = {}
        self._enqueuing = 0
        self._connection_generation = 0
        self._latest_status: StatusEvent | None = None
        self._receive_depth = 0
        self._notify_pending = False

    @property
    def latest_status(self) -> StatusEvent | None:
        """The last received LoRaWAN device-status report, including its timestamp."""
        return self._latest_status

    @property
    def battery_level(self) -> float | None:
        """LoRaWAN battery percentage, or None when unknown or externally powered."""
        status = self._latest_status
        if (
            status is None
            or status.external_power_source
            or status.battery_level_unavailable
        ):
            return None
        return status.battery_level

    @property
    def external_power_source(self) -> bool | None:
        """Whether the last status report indicates external power."""
        status = self._latest_status
        return status.external_power_source if status is not None else None

    @property
    def downlink_margin(self) -> int | None:
        """Device-reported SNR in dB for the received LoRaWAN status request."""
        status = self._latest_status
        return status.margin if status is not None else None

    @property
    def closed(self) -> bool:
        """Whether this model has been retired."""
        return self._closed

    def add_update_listener(self, listener: Callable[[], None]) -> Unsubscribe:
        """Listen for notify calls; read model attributes for initial values."""
        if self._closed:
            raise RuntimeError("Device is closed")
        return subscribe(self._listeners, lambda _: listener())

    def add_remove_listener(self, listener: Callable[[], None]) -> Unsubscribe:
        """Listen for removal or replacement of this model in its collection."""
        if self._closed:
            raise RuntimeError("Device is closed")
        return subscribe(self._remove_listeners, lambda _: listener())

    def _remove(self) -> None:
        """Close a removed model before notifying its observers."""
        listeners, self._remove_listeners = self._remove_listeners, []
        try:
            self.close()
        finally:
            notify(listeners, None)
            listeners.clear()

    def notify(self) -> None:
        """Notify listeners after a complete update, even if state is unchanged."""
        if not self._closed:
            if self._receive_depth:
                self._notify_pending = True
            else:
                notify(self._listeners, None)

    async def async_send_downlink(
        self,
        *,
        data: bytes,
        f_port: int,
        wait_for_ack: bool = True,
        expires_at: datetime | None = None,
    ) -> None:
        """Send a command and wait for its ACK unless explicitly disabled."""
        if self._closed:
            raise DownlinkError("Device is closed")
        if self._send_downlink is None:
            raise DownlinkError("No downlink sender is configured")
        downlink = Downlink(
            self.descriptor.dev_eui, f_port, data, wait_for_ack, expires_at
        )
        if not wait_for_ack:
            await self._send_downlink(downlink)
            return

        # An ACK can reach the event feed before enqueue returns its queue ID.
        generation = self._connection_generation
        self._enqueuing += 1
        try:
            queue_id = await self._send_downlink(downlink)
            if generation != self._connection_generation:
                raise DownlinkError("Connection was lost while sending the command")
            if self._closed:
                raise DownlinkError("Device is closed")
            result = asyncio.get_running_loop().create_future()
            self._pending_acks[queue_id] = result
            if queue_id in self._early_acks:
                result.set_result(self._early_acks.pop(queue_id))
        finally:
            self._enqueuing -= 1
            if not self._enqueuing:
                self._early_acks.clear()
        try:
            if not await result:
                raise DownlinkError("Device did not acknowledge the command")
        finally:
            self._pending_acks.pop(queue_id, None)

    def _handle_ack(self, ack: AckEvent) -> None:
        """Resolve command waits without changing reported device state."""
        if (result := self._pending_acks.get(ack.queue_item_id)) is not None:
            if not result.done():
                result.set_result(ack.acknowledged)
        elif self._enqueuing:
            self._early_acks[ack.queue_item_id] = ack.acknowledged

    def _receive_event(self, event: DeviceEvent) -> None:
        """Store common state and resolve ACKs before the model handles an event."""
        if self._closed:
            return
        self._receive_depth += 1
        try:
            if isinstance(event, StatusEvent):
                self._latest_status = event
                self.notify()
            elif isinstance(event, AckEvent):
                self._handle_ack(event)
            self.handle_event(event)
        finally:
            self._receive_depth -= 1
            if not self._receive_depth and self._notify_pending:
                self._notify_pending = False
                self.notify()

    def handle_event(self, event: DeviceEvent) -> None:
        """Override to decode model data and call notify; no super call is needed."""

    def _connection_lost(self) -> None:
        """Fail commands on a lost connection without retiring this model."""
        self._connection_generation += 1
        for result in self._pending_acks.values():
            if not result.done():
                result.set_exception(
                    DownlinkError(
                        "Connection was lost while waiting for acknowledgement"
                    )
                )
        self._pending_acks.clear()
        self._early_acks.clear()

    def close(self) -> None:
        """Retire the model and release listeners; repeated calls are harmless."""
        self._closed = True
        self._listeners.clear()
        self._remove_listeners.clear()
        for result in self._pending_acks.values():
            if not result.done():
                result.set_exception(DownlinkError("Device is closed"))
        self._pending_acks.clear()
        self._early_acks.clear()
