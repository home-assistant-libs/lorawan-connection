---
title: Devices and collections
description: Model a device's values and commands, then manage devices in a collection.
---

A device model is a Python object representing one physical LoRaWAN device. Its
attributes hold the readings or states reported by that device. Its methods let
you subscribe to changes and, for writable devices, send commands.

Create a subclass of `Device` for each supported model. An `S2101` instance represents
one SenseCAP sensor, with `temperature` and `humidity` attributes. An `LT22222`
instance represents one Dragino relay controller, with two relay states.

## Model the device's data

The `Device` base stores the device descriptor and manages update listeners.
Initialize it with `super().__init__(descriptor)`, then define the data your device
needs. You can use individual attributes, channel dictionaries, nested objects,
or a state dataclass. One message can update several values.

For example, given an S2101 instance named `device`, read its current values directly:

```python
print(device.descriptor.name)
print(device.temperature, device.humidity)
```

Reading these attributes does not contact the device. They contain the latest
values the model has received. Use `None` for measurements that have not arrived;
zero is a valid reading. A partial reading updates only the values it contains.

Implement `handle_event(event)` to interpret incoming events and update these
attributes. The model decides which event types and ports it understands and how
to decode the payload. See [building a device library](/lorawan-connection/patterns/library/)
for the complete S2101 implementation.

## Subscribe to updates

Call `add_update_listener()` to receive notifications from a device:

```python
def show_readings() -> None:
    print(device.temperature, device.humidity)


show_readings()
stop = device.add_update_listener(show_readings)
```

The first call reads the current values. Registering a listener does not replay
state. Later, the model calls `self.notify()` after updating its attributes, and
`show_readings()` runs again. Call `stop()` to unsubscribe.

Listeners take no arguments and run synchronously. Update all affected attributes
before calling `notify()` so listeners see the complete reading. The model decides
whether repeated values need a notification; `notify()` itself does not compare
old and new values.

Freshness rules also belong to the device model. A sleeping LoRaWAN sensor is not
automatically offline. A library can expose a last-seen value or a stale-data rule
when the device's behavior supports it.

## Send commands

A writable device exposes async methods for its operations. For a Dragino LT-22222-L
instance named `relay`, switch its first relay on with:

```python
import asyncio

async with asyncio.timeout(30):
    queue_id = await relay.async_set_relay(1, True)
```

The model encodes the command and sends it through the connection's downlink sender.
The method waits for a positive device acknowledgement and returns the queue ID.
The timeout raises `TimeoutError` if the call takes longer than 30 seconds. This model updates
its reported relay state when an uplink arrives. Subscribe to its updates to observe
that change. See [commands and relays](/lorawan-connection/patterns/commands/) for
sender setup and the complete example.

## Manage devices with a collection

A collection holds the device instances for one logical network. It creates
supported models from inventory events, sends later events to the correct instance,
and removes devices when they leave the inventory.

Declare the supported classes in a `DeviceCollection` subclass:

```python
from lorawan_connection import DeviceCollection
from sensecap_lorawan import S2101


class SenseCapDeviceCollection(DeviceCollection[S2101]):
    DEVICES = (S2101,)


collection = SenseCapDeviceCollection(network_id="my-network")
```

For a one-off collection, pass the classes directly:

```python
collection = DeviceCollection([S2101], network_id="my-network")
```

Each model declares `vendor_id` and `catalog_model_id` as class attributes. The
collection matches both against a device descriptor and rejects duplicate pairs
at construction. `vendor_id` is the numeric LoRa Alliance VendorID.
`catalog_model_id` currently holds the ChirpStack catalog model UUID, distinct from
a QR VendorProfileID. Override `_create_device(descriptor)` only for additional
matching rules.

## Receive devices from the collection

Subscribe to device additions, then forward the event feed to
`collection.handle_event(event)`:

```python
def device_added(device: S2101) -> None:
    print(device.descriptor.name, device.temperature, device.humidity)
    device.add_update_listener(lambda: print(device.temperature, device.humidity))


stop_added = collection.subscribe_device_added(device_added)
```

The subscription reports existing models immediately and future additions as they
arrive. The collection announces a new model before passing its first event to it,
so the callback can attach listeners before readings arrive. The collection creates
devices automatically from descriptors; the caller only feeds events into it.
Current instances are also available in `collection.devices`, keyed by DevEUI.

A metadata update replaces the descriptor on the existing instance. A change to its
vendor or catalog model identity closes that instance and creates a replacement
if the new identity is supported. A removal takes the device out of the collection,
closes it, and calls device-removed listeners registered with `subscribe_device_removed()`.

Calling `device.close()` clears its update listeners and prevents further
notifications. Calling `collection.close()` closes all its devices and clears the
collection's listeners. Both operations are safe to repeat.
