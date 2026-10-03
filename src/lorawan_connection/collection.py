"""Create and retire device models from an inventory and live event feed."""

import logging
from collections.abc import Callable
from typing import Protocol

from .callbacks import Unsubscribe, notify, subscribe
from .events import DeviceDescriptor, DeviceEvent, EventType

_LOGGER = logging.getLogger(__name__)


class Device(Protocol):
    """Minimal model lifecycle required by a collection."""

    descriptor: DeviceDescriptor

    def handle_event(self, event: DeviceEvent) -> None: ...

    def close(self) -> None: ...


class DeviceCollection[DeviceT: Device]:
    """Create vendor models from inventory, then route their live events."""

    def __init__(self, network_id: str) -> None:
        """Own models for exactly one logical network."""
        self.network_id = network_id
        self.devices: dict[str, DeviceT] = {}
        self._added: list[Callable[[DeviceT], None]] = []
        self._removed: list[Callable[[DeviceT], None]] = []
        self._closed = False

    def _create_device(self, descriptor: DeviceDescriptor) -> DeviceT | None:
        raise NotImplementedError

    def subscribe_device_added(
        self, callback: Callable[[DeviceT], None]
    ) -> Unsubscribe:
        """Report existing models immediately, then future additions."""
        if self._closed:
            raise RuntimeError("Device collection is closed")
        stop = subscribe(self._added, callback)
        for device in tuple(self.devices.values()):
            if self.devices.get(device.descriptor.dev_eui) is device:
                notify([callback], device)
        return stop

    def subscribe_device_removed(
        self, callback: Callable[[DeviceT], None]
    ) -> Unsubscribe:
        """Listen for model retirement."""
        if self._closed:
            raise RuntimeError("Device collection is closed")
        return subscribe(self._removed, callback)

    def _remove(self, dev_eui: str) -> None:
        if (device := self.devices.pop(dev_eui, None)) is not None:
            try:
                device.close()
            except Exception:
                _LOGGER.exception("LoRaWAN device cleanup failed")
            notify(self._removed, device)

    def handle_event(self, event: DeviceEvent) -> None:
        """Consume descriptors before activity, never infer a model from data."""
        if self._closed or event.network_id != self.network_id:
            return
        eui = event.dev_eui.replace(":", "").lower()
        if event.type == EventType.REMOVED:
            self._remove(eui)
            return
        device = self.devices.get(eui)
        if event.type in (EventType.ADDED, EventType.UPDATED):
            descriptor = event.descriptor
            if (
                descriptor is None
                or descriptor.network_id != self.network_id
                or descriptor.dev_eui != eui
            ):
                return
            if device is not None and (
                device.descriptor.catalog_model_id,
                device.descriptor.vendor_id,
            ) != (descriptor.catalog_model_id, descriptor.vendor_id):
                self._remove(eui)
                if self._closed:
                    return
                device = None
            if device is None:
                if (device := self._create_device(descriptor)) is None:
                    return
                self.devices[eui] = device
                notify(self._added, device)
            else:
                device.descriptor = descriptor
        if device is not None and self.devices.get(eui) is device:
            device.handle_event(event)

    def close(self) -> None:
        """Retire all models and listeners; repeated calls are harmless."""
        self._closed = True
        for eui in tuple(self.devices):
            self._remove(eui)
        self._added.clear()
        self._removed.clear()
