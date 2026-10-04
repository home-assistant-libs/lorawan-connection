---
title: Understanding events
description: What LoRaWAN events contain and how they reach device models.
---

An event describes a change or activity for one LoRaWAN device. A sensor sending
a temperature reading produces an uplink event. Registering that sensor on the server produces an `ADDED` event
when the connection discovers it.

`lorawan-connection` gives these events a common Python interface. Device libraries
use them to discover supported devices, decode readings, and update their models.

## Which events are there?

Device added, updated, and removed events describe which devices are available and their metadata:

| Event | Meaning |
| --- | --- |
| `ADDED` | A device is among the subscribed devices. This also reports existing devices when a subscription starts. |
| `UPDATED` | A device's metadata changed, such as its name or assigned profile. |
| `REMOVED` | A device is no longer among the subscribed devices. |

Activity events describe messages and reports for those devices:

| Event | Meaning |
| --- | --- |
| `UPLINK` | The device sent application bytes, such as encoded sensor readings. |
| `JOIN` | The device joined the LoRaWAN network. |
| `STATUS` | A device status report, including battery information and radio link margin. |
| `ACK` | The outcome of waiting for a device to acknowledge a confirmed downlink. Check `acknowledged` for the result. |
| `TX_ACK` | A gateway acknowledgement for a downlink transmission. This does not confirm device receipt. |
| `LOG` | A backend log message associated with the device. |
| `LOCATION` | A location update for the device. |

A model handles the events it understands. For example, the S2101 model decodes
`UPLINK` events into temperature and humidity. Another model might also read battery
information from `STATUS`. Sending a command is a separate operation; see
[sending commands](/lorawan-connection/modelling/overview/#send-commands) for downlinks.

## What is inside an event?

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

An activity event carries its payload in `data`. For an uplink, that payload
contains raw application bytes and an FPort. The FPort is the application port;
the device library decides how to interpret it and the bytes.
Without a descriptor, supply the network and device identifiers explicitly.

Here is an uplink event built without a server:

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

`DeviceEventData` holds the common event fields. `UplinkData` holds the uplink
payload. These dataclasses are useful for tests and capture replays. The
[event reference](/lorawan-connection/connection/reference/) lists the fields and
payload types for every event.

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

## Who manages the connection?

The program using the device library owns the connection, credentials, and
reconnection. It connects a collection for each logical network to the backend
subscription. One collection can span several ChirpStack applications; each application
groups devices on the server.

Register a connection-loss listener with `connection.on_disconnect(callback)`.
The callback takes no arguments. Close the old collection when the connection
is lost. A new connection supplies existing devices to a new collection. Events missed during the disconnect are not recovered.

Event callbacks and model listeners run synchronously on the caller's thread or
event loop. Keep network I/O in async connection and command methods. Pass regular
functions as callbacks, and use the collection on one thread.
