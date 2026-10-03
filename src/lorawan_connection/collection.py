"""Create and retire device models from an inventory and live event feed."""

import logging
from collections.abc import Callable, Sequence
from typing import cast

from .callbacks import Unsubscribe, notify, subscribe
from .connection import Connection
from .device import Device
from .events import DeviceDescriptor, DeviceEvent, EventType
from .payloads import Ack

_LOGGER = logging.getLogger(__name__)


class DeviceCollection[DeviceT: Device]:
    """Create vendor models from inventory, then route their live events."""

    DEVICES: Sequence[type[DeviceT]] = ()

    def __init__(
        self,
        connection: Connection,
        models: Sequence[type[DeviceT]] | None = None,
    ) -> None:
        """Own one network; use explicit model classes or the subclass's DEVICES."""
        self._connection = connection
        self._unsubscribe: Unsubscribe | None = None
        self._setup_started = False
        self._send_downlink = connection.async_send_downlink
        self.devices: dict[str, DeviceT] = {}
        self._models: dict[tuple[int, str], type[DeviceT]] = {}
        for model in self.DEVICES if models is None else models:
            identity = (model.vendor_id, model.catalog_model_id)
            if identity in self._models:
                raise ValueError(
                    f"Duplicate catalog identity: vendor_id={identity[0]}, "
                    f"catalog_model_id={identity[1]!r}"
                )
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
            unsubscribe = await self._connection.async_subscribe(
                vendor_ids=frozenset(vendor_id for vendor_id, _ in self._models),
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
        """Construct a registered model, or leave an unknown identity unsupported."""
        if descriptor.vendor_id is None:
            return None
        model = self._models.get((descriptor.vendor_id, descriptor.catalog_model_id))
        if model is None:
            return None
        return model(descriptor)

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
            if (
                descriptor is None
                or descriptor.network_id != event.network_id
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
                device._send_downlink = self._send_downlink
                self.devices[eui] = device
                notify(self._added, device)
            else:
                device.descriptor = descriptor
        if device is not None and self.devices.get(eui) is device:
            if event.type == EventType.ACK and event.data is not None:
                device._handle_ack(cast(Ack, event.data))
            device.handle_event(event)

    def close(self) -> None:
        """Retire all models and listeners; repeated calls are harmless."""
        self._closed = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        for eui in tuple(self.devices):
            self._remove(eui, removed=False)
        self._added.clear()
        self._removed.clear()
