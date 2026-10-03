---
title: Collections and models
description: Model lifecycle belongs to the collection; decoding belongs to each device.
---

Subclass `DeviceCollection[YourDevice]` and implement `_create_device(descriptor)`.
Return a model for a supported catalog identity, or `None` for an unsupported one.

```python
from lorawan_connection import DeviceCollection, DeviceDescriptor
from .models import S2101


DEVICE_MODELS: dict[tuple[int | None, str], type[S2101]] = {
    (0x02E8, "fc455aa2-01cf-492b-9359-a5d8c9a0e1b3"): S2101,
}


class SenseCapDeviceCollection(DeviceCollection[S2101]):
    def _create_device(self, descriptor: DeviceDescriptor) -> S2101 | None:
        model_class = DEVICE_MODELS.get(
            (descriptor.vendor_id, descriptor.catalog_model_id)
        )
        return model_class(descriptor) if model_class is not None else None
```

The caller feeds all events to `collection.handle_event(event)`. It never checks
whether a model exists or calls an `add_device` method.

## Model lifecycle

An inventory event creates a supported model. The collection stores it and calls
device-added listeners before passing that event to the model. The consumer can
attach state listeners before the model processes its first event.

A metadata update replaces the model's descriptor. Renaming a device or changing
its server profile ID does not replace a model with the same catalog identity.
A changed vendor ID or catalog model ID retires the old model. The factory then
decides whether the replacement identity is supported.

A removal calls `device.close()` and then device-removed listeners. The model has
already left the collection when those listeners run. Closing the collection
retires all models and clears collection listeners.

## Model state

Each model has a writable `descriptor` attribute, a `handle_event(event)` method,
and a `close()` method. Other state and methods belong to the vendor library.
Use frozen state dataclasses and notify listeners only when values change.

Use `None` for an unobserved measurement. Preserve zero readings. A partial uplink
updates only the measurements it contains. Device models interpret ports, status,
and other event types; consumers should not duplicate that logic.

The collection does not define state freshness or device availability. A sleeping
LoRaWAN sensor is not automatically offline. A library can expose a last-seen value
or a documented stale-data rule when the device's behavior supports it.

See [decoding and state](/lorawan-connection/patterns/decoding/) for the complete
SenseCAP example and its limits.
