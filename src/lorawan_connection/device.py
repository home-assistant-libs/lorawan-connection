"""Device identity and synchronous update notifications."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import ClassVar

from .callbacks import Unsubscribe, notify, subscribe
from .downlink import Downlink, DownlinkError, SendDownlink
from .events import DeviceDescriptor, DeviceEvent
from .payloads import Ack


class Device(ABC):
    """Own identity and listeners; subclasses define data and interpret events."""

    identifiers: ClassVar[Mapping[str, tuple[int | str, str]]]

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        self.descriptor = descriptor
        self._listeners: list[Callable[[None], None]] = []
        self._remove_listeners: list[Callable[[None], None]] = []
        self._closed = False
        self._send_downlink: SendDownlink | None = None
        self._pending_acks: dict[str, asyncio.Future[bool]] = {}
        self._early_acks: dict[str, bool] = {}
        self._enqueuing = 0

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
        self._enqueuing += 1
        try:
            queue_id = await self._send_downlink(downlink)
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

    def _handle_ack(self, ack: Ack) -> None:
        """Resolve command waits without changing reported device state."""
        if (result := self._pending_acks.get(ack.queue_item_id)) is not None:
            if not result.done():
                result.set_result(ack.acknowledged)
        elif self._enqueuing:
            self._early_acks[ack.queue_item_id] = ack.acknowledged

    @abstractmethod
    def handle_event(self, event: DeviceEvent) -> None:
        """Interpret an event, commit state, then notify listeners as needed."""

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
