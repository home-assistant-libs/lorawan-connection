---
title: Understanding events
description: Typed LoRaWAN events, their fields, and how they reach device models.
---

Version 0.11 introduces these event classes in place of the envelope API from 0.10.
See the [migration notes](/lorawan-connection/connection/reference/#changes-from-010).

Backends deliver inventory and activity as frozen, slotted dataclasses.
`DeviceEvent` is the union of these classes. Import them from `lorawan_connection`.
Each class fixes its own `type`; callers do not pass it to the constructor.

## Shared fields

Every event exposes these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `network_id` | `str` | Application-owned identity of the logical network. |
| `dev_eui` | `str` | Device identifier, normalized to 16 lowercase hexadecimal characters. |
| `received_at` | `datetime` | Time the server received the event. Backends supply a timezone-aware timestamp. |
| `type` | `EventType` | Fixed by the event class. |
| `descriptor` | `DeviceDescriptor \| None` | Inventory metadata, when available. |

All constructor arguments are keyword-only. Inventory events take `descriptor`
and `received_at`. Their `network_id` and `dev_eui` are properties of the descriptor,
not constructor arguments.

Activity events accept a descriptor or explicit `network_id` and `dev_eui` values.
With a descriptor, identifiers are derived from it. Conflicting explicit values
raise `ValueError`. Without one, both identifiers are required.

## Inventory events

Both constructor fields are required. The
[descriptor reference](/lorawan-connection/connection/reference/#devicedescriptor)
lists the device's name, server application, profile, and catalog fields.

### AddedEvent

A device is available to the subscriber. Subscriptions also replay existing
devices with this event.

Fixed type: `EventType.ADDED` (`"added"`).

| Field | Type | Meaning |
| --- | --- | --- |
| `descriptor` | `DeviceDescriptor` | Current device metadata. |
| `received_at` | `datetime` | Event timestamp. |

```python
from datetime import UTC, datetime

from lorawan_connection import AddedEvent

added = AddedEvent(descriptor=descriptor, received_at=datetime.now(UTC))
assert added.dev_eui == descriptor.dev_eui
```

### UpdatedEvent

Device metadata changed, such as its name or assigned profile.

Fixed type: `EventType.UPDATED` (`"updated"`).

| Field | Type | Meaning |
| --- | --- | --- |
| `descriptor` | `DeviceDescriptor` | Updated device metadata. |
| `received_at` | `datetime` | Event timestamp. |

### RemovedEvent

A device left the subscription.

Fixed type: `EventType.REMOVED` (`"removed"`).

| Field | Type | Meaning |
| --- | --- | --- |
| `descriptor` | `DeviceDescriptor` | Last known device metadata. |
| `received_at` | `datetime` | Event timestamp. |

## Activity events

Each class adds the fields below to the [shared fields](#shared-fields).
`received_at` is required. `descriptor` defaults to `None`; supply either a
descriptor or both `network_id` and `dev_eui`.

Backends convert SDK messages into these classes before delivery. Use the same
classes for live events, tests, and capture replays.

### UplinkEvent

Raw application bytes received from a device. The device library decides which
ports and byte layouts it supports. `data` retains the bytes without copying;
mutable SDK messages are not exposed.

Fixed type: `EventType.UPLINK` (`"up"`).

| Field | Type | Default |
| --- | --- | --- |
| `data` | `bytes` | Required |
| `f_port` | `int` | `1` |

```python
from datetime import UTC, datetime

from lorawan_connection import UplinkEvent

event = UplinkEvent(
    network_id="home",
    dev_eui="0201010101010101",
    received_at=datetime.now(UTC),
    data=bytes.fromhex("01011098530000010210A87A0000AF51"),
    f_port=1,
)
```

### JoinEvent

A device joined the network. `dev_addr` is the joined device address.

Fixed type: `EventType.JOIN` (`"join"`).

| Field | Type | Default |
| --- | --- | --- |
| `dev_addr` | `str` | Required |

### StatusEvent

Device battery and radio link status. `margin` is the radio link margin in dB.
`battery_level` is a percentage; check the power-source and battery-unavailable
flags before reading it. Zero with `battery_level_unavailable=False` is a known
zero, not a missing reading.

Fixed type: `EventType.STATUS` (`"status"`).

| Field | Type | Default |
| --- | --- | --- |
| `margin` | `int` | `0` |
| `external_power_source` | `bool` | `False` |
| `battery_level_unavailable` | `bool` | `True` |
| `battery_level` | `float` | `0` |

The base `Device` retains this report and notifies listeners automatically. See
[status properties](/lorawan-connection/modelling/reference/#status-properties).

### AckEvent

The outcome of a confirmed downlink. Match `queue_item_id` to the returned command
ID and check `acknowledged`.

Fixed type: `EventType.ACK` (`"ack"`).

| Field | Type | Default |
| --- | --- | --- |
| `queue_item_id` | `str` | Required |
| `acknowledged` | `bool` | Required |

See [sending commands](/lorawan-connection/modelling/overview/#send-commands)
for command submission.

### TxAckEvent

A gateway acknowledged transmission. This does not confirm device receipt.

Fixed type: `EventType.TX_ACK` (`"txack"`).

| Field | Type | Default |
| --- | --- | --- |
| `gateway_id` | `str` | Required |
| `downlink_id` | `int` | Required |

### LogEvent

A backend log message. `level` and `code` retain the backend's numeric values.

Fixed type: `EventType.LOG` (`"log"`).

| Field | Type | Default |
| --- | --- | --- |
| `description` | `str` | Required |
| `level` | `int` | Required |
| `code` | `int` | Required |

### LocationEvent

A device location estimate. `latitude` and `longitude` use degrees;
`altitude` uses meters.

Fixed type: `EventType.LOCATION` (`"location"`).

| Field | Type | Default |
| --- | --- | --- |
| `latitude` | `float` | Required |
| `longitude` | `float` | Required |
| `altitude` | `float` | Required |

## How events reach a device

Call `await collection.async_setup()` to subscribe to events for the vendors
represented by the collection's model classes. A collection without model classes
creates generic devices for all inventory exposed by the connection. The collection creates supported
models and routes each event to its device automatically. The
[ChirpStack connection example](/lorawan-connection/connection/chirpstack/)
shows connection startup and collection setup.

The subscription first delivers existing devices as `ADDED` events. For each
supported device, the collection creates a model and calls its device-added
listeners. Applications use those listeners to observe the models and subscribe
to state updates before the first reading arrives.

Later activity goes to the existing model. An uplink can update several attributes,
then the model calls `notify()` so listeners can read the new values. Device
changes arrive through the same feed; `REMOVED` closes and removes the model.
The [quickstart](/lorawan-connection/getting-started/quickstart/) shows this sequence
with a descriptor and a captured uplink.

The descriptor must arrive before activity for an unknown device. If the backend
first learns about a device through activity, it refreshes its device list before
forwarding that activity. Collections ignore activity for devices they have not
created. A backend only reports removals after a complete successful device-list
refresh; an incomplete read must not make devices disappear.

## Connection ownership

The application owns credentials and the connection lifecycle. Create one collection
per logical network; a collection can span several server applications.

Register a connection-loss listener with `connection.on_disconnect(callback)`.
The callback takes no arguments. Close the old collection when the connection
is lost. A new connection supplies existing devices to a new collection. Events missed during the disconnect are not recovered.

Event callbacks and model listeners run synchronously on the caller's thread or
event loop. Keep network I/O in async connection and command methods. Pass regular
functions as callbacks, and use the collection on one thread.
