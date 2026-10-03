"""Device identity, typed state, and synchronous update notifications."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar

from .callbacks import Unsubscribe, notify, subscribe
from .events import DeviceDescriptor, DeviceEvent


class Device[StateT](ABC):
    """Own model state and listeners; subclasses interpret incoming events."""

    vendor_id: ClassVar[int]
    catalog_model_id: ClassVar[str]

    def __init__(self, descriptor: DeviceDescriptor, *, state: StateT) -> None:
        self.descriptor = descriptor
        self.state = state
        self._listeners: list[Callable[[None], None]] = []
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether this model has been retired."""
        return self._closed

    def add_update_listener(self, listener: Callable[[], None]) -> Unsubscribe:
        """Listen for notify calls; read state directly for initial values."""
        if self._closed:
            raise RuntimeError("Device is closed")
        return subscribe(self._listeners, lambda _: listener())

    def notify(self) -> None:
        """Notify listeners after a complete update, even if state is unchanged."""
        if not self._closed:
            notify(self._listeners, None)

    @abstractmethod
    def handle_event(self, event: DeviceEvent) -> None:
        """Interpret an event, commit state, then notify listeners as needed."""

    def close(self) -> None:
        """Retire the model and release listeners; repeated calls are harmless."""
        self._closed = True
        self._listeners.clear()
