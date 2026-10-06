---
title: Understanding events
description: What LoRaWAN events contain and how they reach device models.
---

Backends deliver device inventory and activity through a common event interface.
Collections use inventory events to create and remove models. Models decode
activity events into readings and command results.

## Event types

Inventory events:

| Event | Meaning |
| --- | --- |
| `ADDED` | A device is among the subscribed devices. This also reports existing devices when a subscription starts. |
| `UPDATED` | A device's metadata changed, such as its name or assigned profile. |
| `REMOVED` | A device is no longer among the subscribed devices. |

Activity events:

| Event | Meaning |
| --- | --- |
| `UPLINK` | The device sent application bytes, such as encoded sensor readings. |
| `JOIN` | The device joined the LoRaWAN network. |
| `STATUS` | A device status report, including battery information and radio link margin. |
| `ACK` | The outcome of waiting for a device to acknowledge a confirmed downlink. Check `acknowledged` for the result. |
| `TX_ACK` | A gateway acknowledgement for a downlink transmission. This does not confirm device receipt. |
| `LOG` | A backend log message associated with the device. |
| `LOCATION` | A location update for the device. |

Sending a command is a separate operation; see
[sending commands](/lorawan-connection/modelling/overview/#send-commands).

## Event fields

Every event identifies its network and device, gives its `EventType`, and includes
a timezone-aware `received_at` timestamp. The device identifier is its DevEUI.
The network identifier distinguishes one logical network from another.

An `ADDED` or `UPDATED` event also carries a `DeviceDescriptor`. This describes the
device's name, server application, profile, and catalog identity. The collection
uses the catalog identity to choose a device class.

Pass the descriptor when constructing that event. The event takes its `dev_eui`
and `network_id` from the descriptor:

```python
from datetime import UTC, datetime

from lorawan_connection import DeviceEventData, EventType

added = DeviceEventData(
    type=EventType.ADDED,
    received_at=datetime.now(UTC),
    descriptor=descriptor,
)
```

Activity payloads go in `data`. An uplink carries raw application bytes and the
application port, `f_port`. Without a descriptor, supply the identifiers explicitly:

```python
from datetime import UTC, datetime

from lorawan_connection import DeviceEventData, EventType, UplinkData

event = DeviceEventData(
    network_id="home",
    dev_eui="0201010101010101",
    type=EventType.UPLINK,
    received_at=datetime.now(UTC),
    data=UplinkData(
        data=bytes.fromhex("01011098530000010210A87A0000AF51"),
        f_port=1,
    ),
)
```

Use these dataclasses for tests and capture replays. The
[event reference](/lorawan-connection/connection/reference/) lists all fields and payload types.

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
