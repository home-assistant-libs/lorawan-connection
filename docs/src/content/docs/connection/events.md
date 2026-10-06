---
title: Understanding events
description: Typed LoRaWAN events, their fields, and how they reach device models.
---

These event classes are prepared for 0.11.0. PyPI 0.10.0 uses the former envelope API.
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

| Class | Fixed type | Required fields | Meaning |
| --- | --- | --- | --- |
| `AddedEvent` | `ADDED` (`"added"`) | `descriptor`, `received_at` | A device is available to the subscriber. Also replays existing devices when a subscription starts. |
| `UpdatedEvent` | `UPDATED` (`"updated"`) | `descriptor`, `received_at` | Device metadata changed, such as its name or assigned profile. |
| `RemovedEvent` | `REMOVED` (`"removed"`) | `descriptor`, `received_at` | A device left the subscription. Carries its last descriptor. |

```python
from datetime import UTC, datetime

from lorawan_connection import AddedEvent

added = AddedEvent(descriptor=descriptor, received_at=datetime.now(UTC))
assert added.dev_eui == descriptor.dev_eui
```

The [descriptor reference](/lorawan-connection/connection/reference/#devicedescriptor)
lists its name, server application, profile, and catalog fields.

## Activity events

The fields below are direct attributes of each event, in addition to the shared
fields above. Fields are required unless a default is shown.

| Class | Fixed type | Fields |
| --- | --- | --- |
| `UplinkEvent` | `UPLINK` (`"up"`) | `data: bytes`, `f_port: int = 1` |
| `JoinEvent` | `JOIN` (`"join"`) | `dev_addr: str` |
| `StatusEvent` | `STATUS` (`"status"`) | `margin: int = 0`, `external_power_source: bool = False`, `battery_level_unavailable: bool = True`, `battery_level: float = 0` |
| `AckEvent` | `ACK` (`"ack"`) | `queue_item_id: str`, `acknowledged: bool` |
| `TxAckEvent` | `TX_ACK` (`"txack"`) | `gateway_id: str`, `downlink_id: int` |
| `LogEvent` | `LOG` (`"log"`) | `description: str`, `level: int`, `code: int` |
| `LocationEvent` | `LOCATION` (`"location"`) | `latitude: float`, `longitude: float`, `altitude: float` |

`UplinkEvent.data` contains raw application bytes. The device library decides which
ports and byte layouts it supports. `JoinEvent.dev_addr` is the joined device address.

`StatusEvent.margin` is the radio link margin in dB. Check the power-source and
battery-unavailable flags before reading `battery_level`, which is a percentage.
Zero with `battery_level_unavailable=False` is a known zero, not a missing reading.

`AckEvent` reports the outcome of a confirmed downlink. Match `queue_item_id` to
the returned command ID and check `acknowledged`. A `TxAckEvent` reports gateway
transmission; it does not confirm device receipt.

`LogEvent.level` and `code` retain the backend's numeric values.
`LocationEvent.latitude` and `longitude` use degrees; `altitude` uses meters.

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

Use the same classes for live events, tests, and capture replays. Backends convert
SDK messages into these classes before delivery. Raw application bytes are retained
without copying; mutable SDK messages are not exposed.

Sending a command is a separate operation; see
[sending commands](/lorawan-connection/modelling/overview/#send-commands).

## How events reach a device

Call `await collection.async_setup()` to subscribe to events for the vendors
represented by the collection's model classes. The collection creates supported
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
