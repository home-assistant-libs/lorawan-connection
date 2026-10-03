"""The connection interface consumed by device collections."""

from collections.abc import Callable
from typing import Protocol

from .callbacks import Unsubscribe
from .downlink import Downlink
from .events import DeviceEvent


class ConnectionUnavailable(Exception):
    """The connection cannot deliver device events."""


class Connection(Protocol):
    """Subscribe to a network's devices and send commands."""

    async def async_subscribe(
        self,
        *,
        vendor_ids: frozenset[int],
        callback: Callable[[DeviceEvent], None],
    ) -> Unsubscribe:
        """Deliver matching existing devices before returning, then live events."""
        ...

    def on_disconnect(self, callback: Callable[[], None]) -> Unsubscribe:
        """Listen for connection loss; return a function to remove the listener."""
        ...

    async def async_send_downlink(self, downlink: Downlink) -> str:
        """Queue a downlink and return its ID for acknowledgement correlation."""
        ...
