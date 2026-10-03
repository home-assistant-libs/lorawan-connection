---
title: Ownership and event delivery
description: How providers deliver descriptors and live events to device libraries.
---

The application owns the network connection and forwards events to the vendor
library. Keep endpoints and credentials in the application.

One collection contains the device models for a logical network. It can span
several server applications.

## Inventory before activity

A provider sends an `ADDED` event with a descriptor for each existing device when
a consumer subscribes. The consumer can create models before the first uplink.
Later, the same feed carries inventory changes and live activity.

| Event | Meaning |
| --- | --- |
| `ADDED` | This device is in the subscribed inventory. |
| `UPDATED` | Its current descriptor replaces the previous descriptor. |
| `REMOVED` | This device has left the subscribed inventory. |
| Other event types | Activity for a device already in the inventory. |

Collections ignore activity for an unknown device. A provider that discovers an
unknown device through activity first refreshes inventory. It sends the descriptor
before forwarding that activity. It does not guess a model from bytes.

The provider only reports removals after a complete successful inventory refresh.
A failed page or partial response must not make devices disappear.

## Identity and model selection

Use `(network_id, dev_eui)` as device identity. `network_id` is a stable identifier
owned by the application. Changing credentials or reconnecting does not change it.
Descriptors normalize the DevEUI to 16 lowercase hexadecimal characters.

`profile_id` identifies the server profile. Use `catalog_model_id` and `vendor_id`
to select a supported model. A profile name alone cannot establish model identity.

## Borrowed payloads

`DeviceEvent` and payload types are read-only `Protocol`s. A backend can supply its
own objects when their fields and meanings match the contract.

A generated ChirpStack uplink satisfies `Uplink`. It does not satisfy the complete
`DeviceEvent`: the shared envelope also needs a network ID, event kind, and Python
timestamp. Wrap the payload in `DeviceEventData`; the payload object is not copied.

```python
from lorawan_connection import DeviceEventData, EventType

# message is an existing generated UplinkEvent from the provider.
event = DeviceEventData(
    network_id=network_id,
    dev_eui=message.device_info.dev_eui,
    type=EventType.UPLINK,
    received_at=received_at,
    data=message,
)
assert event.data is message
```

Protocols do not freeze generated messages. Providers and consumers must not mutate
a payload after delivery while a consumer can still hold it. Use `EventType` to
select the payload contract. Overlapping fields cannot identify an event type.

## Live delivery and connection loss

The package does not promise historical replay, durable delivery, or recovery of
missed events. `received_at` is a timezone-aware receipt timestamp. It is not a
deduplication key; two updates can have the same timestamp.

Connection loss belongs to the provider subscription. It is not a `REMOVED` event
for every device. The application closes the old collection when its subscription
ends. A new subscription supplies inventory to a new collection.

Callbacks run synchronously on the caller's thread or event loop. Do not block,
perform network I/O, or pass an async callback. The package is not thread-safe.
