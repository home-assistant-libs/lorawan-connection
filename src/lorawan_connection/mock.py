"""In-memory connection for device-library tests and capture replays."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from .callbacks import Unsubscribe, notify, subscribe
from .connection import ConnectionUnavailable
from .downlink import Downlink, DownlinkError
from .events import DeviceDescriptor, DeviceEvent, DeviceEventData, EventType


@dataclass(eq=False)
class _Subscriber:
    vendor_ids: frozenset[int]
    callback: Callable[[DeviceEvent], None]


class MockConnection:
    """Replay devices, emit events, and record commands without network I/O."""

    def __init__(self, devices: Sequence[DeviceDescriptor] = ()) -> None:
        self.devices: dict[str, DeviceDescriptor] = {}
        self.downlinks: dict[str, Downlink] = {}
        self._network_id: str | None = None
        self._available = True
        self._subscribers: list[_Subscriber] = []
        self._disconnect_listeners: list[Callable[[None], None]] = []
        self._queue_id = 0
        for descriptor in devices:
            self.emit(
                DeviceEventData(
                    type=EventType.ADDED,
                    received_at=datetime.now(UTC),
                    descriptor=descriptor,
                )
            )

    async def async_subscribe(
        self,
        *,
        vendor_ids: frozenset[int],
        callback: Callable[[DeviceEvent], None],
    ) -> Unsubscribe:
        """Deliver existing matching devices, then subscribe to future events."""
        self._check_available()
        subscriber = _Subscriber(vendor_ids, callback)
        self._subscribers.append(subscriber)
        for descriptor in tuple(self.devices.values()):
            if subscriber not in self._subscribers:
                break
            if descriptor.vendor_id in vendor_ids:
                notify(
                    [callback],
                    DeviceEventData(
                        type=EventType.ADDED,
                        received_at=datetime.now(UTC),
                        descriptor=descriptor,
                    ),
                )

        def unsubscribe() -> None:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

        return unsubscribe

    def on_disconnect(self, callback: Callable[[], None]) -> Unsubscribe:
        """Listen for simulated connection loss."""
        self._check_available()
        return subscribe(self._disconnect_listeners, lambda _: callback())

    async def async_send_downlink(self, downlink: Downlink) -> str:
        """Record a command under its queue ID; acknowledgements are explicit."""
        if not self._available:
            raise DownlinkError("Mock connection is disconnected")
        if downlink.dev_eui not in self.devices:
            raise DownlinkError("Unknown device")
        self._queue_id += 1
        queue_id = str(self._queue_id)
        self.downlinks[queue_id] = downlink
        return queue_id

    def emit(self, event: DeviceEvent) -> None:
        """Update the device list and deliver an event to matching subscribers."""
        self._check_available()
        if self._network_id is not None and event.network_id != self._network_id:
            raise ValueError("Event belongs to another network")
        dev_eui = event.dev_eui.replace(":", "").lower()
        previous = self.devices.get(dev_eui)
        descriptor = previous
        if event.type in (EventType.ADDED, EventType.UPDATED):
            descriptor = event.descriptor
            if (
                descriptor is None
                or descriptor.dev_eui != dev_eui
                or descriptor.network_id != event.network_id
            ):
                raise ValueError("Device event requires a matching descriptor")
            self.devices[dev_eui] = descriptor
        if descriptor is None:
            raise ValueError("Add the device before emitting its events")
        self._network_id = event.network_id
        if event.type == EventType.REMOVED:
            del self.devices[dev_eui]
        for subscriber in tuple(self._subscribers):
            if subscriber not in self._subscribers:
                continue
            if descriptor.vendor_id in subscriber.vendor_ids:
                notify([subscriber.callback], event)
            elif previous is not None and previous.vendor_id in subscriber.vendor_ids:
                notify(
                    [subscriber.callback],
                    DeviceEventData(
                        type=EventType.REMOVED,
                        received_at=event.received_at,
                        descriptor=previous,
                    ),
                )

    def disconnect(self) -> None:
        """Stop event delivery and notify connection-loss listeners once."""
        if not self._available:
            return
        self._available = False
        self._subscribers.clear()
        listeners, self._disconnect_listeners = self._disconnect_listeners, []
        notify(listeners, None)
        listeners.clear()

    def _check_available(self) -> None:
        if not self._available:
            raise ConnectionUnavailable("Mock connection is disconnected")
