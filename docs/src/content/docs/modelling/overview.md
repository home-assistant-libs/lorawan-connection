---
title: Collections and models
description: Model lifecycle belongs to the collection; decoding belongs to each device.
---

Define each model with `Device`, then declare the supported classes on your
collection. The base collection builds the catalog lookup and creates models:

```python
from lorawan_connection import DeviceCollection
from .models import S2101


class SenseCapDeviceCollection(DeviceCollection[S2101]):
    DEVICES = (S2101,)


models = SenseCapDeviceCollection(network_id="my-network")
```

For a one-off collection, pass the classes directly:

```python
models = DeviceCollection([S2101], network_id="my-network")
```

Models declare `vendor_id` and `catalog_model_id` as class attributes. The collection
matches both against the descriptor and rejects duplicate pairs at construction.
`vendor_id` is the numeric LoRa Alliance VendorID. `catalog_model_id` currently holds
the ChirpStack catalog model UUID; it is distinct from a QR VendorProfileID.
Override `_create_device(descriptor)` only when you need additional matching rules.

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

The `Device` base stores identity and listeners. Initialize it with
`super().__init__(descriptor)`. Each vendor model defines its data: individual
attributes, channel collections, nested objects, or a state dataclass.
Implement `handle_event(event)` to decode data and call `self.notify()` after an update.
One event can update any number of attributes.

```python
stop = device.add_update_listener(lambda: print(device.temperature, device.humidity))
```

Listeners take no arguments. Registration does not replay state; read model attributes
for initial values. `notify()` calls listeners even if state is unchanged, so the
model decides whether a repeated reading needs notification. Commit a complete update
before notifying. The base `close()` clears listeners and prevents further notifications.

Use `None` for an unobserved measurement. Preserve zero readings. A partial uplink
updates only the measurements it contains. Device models interpret ports, status,
and other event types; consumers should not duplicate that logic.

The collection does not define state freshness or device availability. A sleeping
LoRaWAN sensor is not automatically offline. A library can expose a last-seen value
or a documented stale-data rule when the device's behavior supports it.

See [decoding and state](/lorawan-connection/patterns/decoding/) for the complete
SenseCAP example and its limits.
