"""Create and retire device models from an inventory and live event feed."""

import logging
from collections.abc import Callable, Sequence
from typing import cast, overload

from .callbacks import Unsubscribe, notify, subscribe
from .connection import Connection
from .device import Device
from .events import DeviceDescriptor, DeviceEvent, EventType

_LOGGER = logging.getLogger(__name__)


class DeviceCollection[DeviceT: Device]:
    """Create vendor models from inventory, then route their live events."""

    DEVICES: Sequence[type[DeviceT]] = ()

    @overload
    def __init__(
        self: "DeviceCollection[Device]",
        connection: Connection,
        models: None = None,
    ) -> None: ...

    @overload
    def __init__(
        self,
        connection: Connection,
        models: Sequence[type[DeviceT]] | None = None,
    ) -> None: ...

    def __init__(
        self,
        connection: Connection,
        models: Sequence[type[DeviceT]] | None = None,
    ) -> None:
        """Own one network; use explicit model classes or the subclass's DEVICES."""
        self._connection = connection
        self._generic = models is None and not self.DEVICES
        self._unsubscribe: Unsubscribe | None = None
        self._unsubscribe_disconnect: Unsubscribe | None = None
        self._setup_started = False
        self._send_downlink = connection.async_send_downlink
        self.devices: dict[str, DeviceT] = {}
        self._models: dict[tuple[str, int | str, str], type[DeviceT]] = {}
        for model in self.DEVICES if models is None else models:
            for stack, (brand_id, model_id) in model.identifiers.items():
                identity = (stack, brand_id, model_id)
                if identity in self._models:
                    raise ValueError(f"Duplicate model identity: {identity!r}")
                self._models[identity] = model
        self._added: list[Callable[[DeviceT], None]] = []
        self._removed: list[Callable[[DeviceT], None]] = []
        self._closed = False

    async def async_setup(self) -> None:
        """Subscribe to the vendors represented by the registered models."""
        if self._closed or self._setup_started:
            raise RuntimeError("Device collection is closed or already set up")
        self._setup_started = True
        try:
            self._unsubscribe_disconnect = self._connection.on_disconnect(
                self._connection_lost
            )
            unsubscribe = await self._connection.async_subscribe(
                brands=None
                if self._generic
                else frozenset(
                    (stack, brand_id) for stack, brand_id, _ in self._models
                ),
                callback=self.handle_event,
            )
        except BaseException:
            self.close()
            raise
        if self._closed:
            unsubscribe()
            raise RuntimeError("Device collection was closed during setup")
        self._unsubscribe = unsubscribe

    def _create_device(self, descriptor: DeviceDescriptor) -> DeviceT | None:
        """Construct a generic device or match a registered model."""
        if self._generic:
            return cast(DeviceT, Device(descriptor))
        model = (
            self._models.get(
                (descriptor.stack, descriptor.brand_id, descriptor.model_id)
            )
            if descriptor.brand_id is not None
            else None
        )
        return model(descriptor) if model is not None else None

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

    def _remove(self, dev_eui: str, *, removed: bool = True) -> None:
        if (device := self.devices.pop(dev_eui, None)) is not None:
            try:
                if removed:
                    device._remove()
                else:
                    device.close()
            except Exception:
                _LOGGER.exception("LoRaWAN device cleanup failed")
            notify(self._removed, device)

    def handle_event(self, event: DeviceEvent) -> None:
        """Consume descriptors before activity, never infer a model from data."""
        if self._closed:
            return
        eui = event.dev_eui.replace(":", "").lower()
        if event.type == EventType.REMOVED:
            self._remove(eui)
            return
        device = self.devices.get(eui)
        if event.type in (EventType.ADDED, EventType.UPDATED):
            descriptor = event.descriptor
            if device is not None and (
                device.descriptor.stack,
                device.descriptor.model_id,
                device.descriptor.brand_id,
            ) != (descriptor.stack, descriptor.model_id, descriptor.brand_id):
                self._remove(eui)
                if self._closed:
                    return
                device = None
            if device is None:
                if (device := self._create_device(descriptor)) is None:
                    return
                device._send_downlink = self._send_downlink
                self.devices[eui] = device
                notify(self._added, device)
            elif device.descriptor != descriptor:
                device.descriptor = descriptor
                device.notify()
        if device is not None and self.devices.get(eui) is device:
            device._receive_event(event)

    def _connection_lost(self) -> None:
        for device in tuple(self.devices.values()):
            device._connection_lost()

    def close(self) -> None:
        """Retire all models and listeners; repeated calls are harmless."""
        self._closed = True
        if self._unsubscribe_disconnect is not None:
            self._unsubscribe_disconnect()
            self._unsubscribe_disconnect = None
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        for eui in tuple(self.devices):
            self._remove(eui, removed=False)
        self._added.clear()
        self._removed.clear()
