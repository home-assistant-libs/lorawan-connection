---
title: Event reference
description: Descriptor fields, typed event dispatch, and migration from envelope events.
---

Import these types from `lorawan_connection`. For transport and subscription requirements, see
[Add a backend](/lorawan-connection/connection/adding-a-backend/).

## DeviceDescriptor

A frozen, slotted dataclass. Positional or keyword arguments follow this order:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `network_id` | `str` | Required | Stable application-owned network identity. |
| `stack` | `str` | Required, keyword-only | Catalog namespace, such as `chirpstack` or `tts`. |
| `dev_eui` | `str` | Required | Eight-byte device identifier. |
| `name` | `str` | Required | Current display name. |
| `application_id` | `str` | Required | Server application identity. |
| `profile_id` | `str` | Required | Server profile identity, or an empty string when the stack has no equivalent. |
| `model_id` | `str` | `""` | Reviewed catalog model identity, if known. |
| `brand_id` | `int \| str \| None` | `None` | Native brand identifier within the stack. |
| `model` | `str` | `""` | Model display text. |
| `manufacturer` | `str` | `""` | Manufacturer display text. |

Construction removes colons from `dev_eui` and lowercases it. The result must have
exactly 16 hexadecimal characters; otherwise construction raises `ValueError`.
Other fields are supplied by the provider and are not validated by this dataclass.

## DeviceEvent

`DeviceEvent` is a union of `AddedEvent`, `UpdatedEvent`, `RemovedEvent`,
`UplinkEvent`, `JoinEvent`, `StatusEvent`, `AckEvent`, `TxAckEvent`, `LogEvent`,
and `LocationEvent`. Construct one of these classes; the union itself is not a constructor.

The [events page](/lorawan-connection/connection/events/) lists every field,
default, and event type. `EventType` remains a `StrEnum` with the same string values.
All event classes are frozen and slotted. Their `type` field is fixed at construction
and cannot be supplied or changed with `dataclasses.replace()`.

Use the event class or its discriminator to narrow the type. No payload cast is needed:

```python
from lorawan_connection import DeviceEvent, EventType


def handle_event(event: DeviceEvent) -> None:
    if event.type == EventType.UPLINK:
        print(event.f_port, event.data.hex())
    elif event.type == EventType.ACK:
        print(event.queue_item_id, event.acknowledged)
```

Inventory constructors accept only `descriptor` and `received_at`. Their identity
properties read from the descriptor. Activity constructors accept a descriptor or
explicit network and device identifiers. Explicit identifiers must match a supplied
descriptor. Construction normalizes DevEUIs and rejects invalid ones.

Timestamps and radio ranges are not validated by the dataclasses. Backends provide
timezone-aware timestamps; device libraries validate wire formats and measurements.

## Changes from 0.10

The typed event API is prepared for the next release. The current PyPI release,
0.10.0, still uses the envelope and payload API.

Replace `DeviceEventData(type=EventType.UPLINK, data=UplinkData(...), ...)`
with `UplinkEvent(data=..., f_port=..., ...)`. Other activity events follow the same
pattern. Location coordinates move directly onto `LocationEvent`.

Replace inventory envelopes with `AddedEvent`, `UpdatedEvent`, or `RemovedEvent`.
Pass the descriptor and timestamp; omit identifiers and the `type` argument.
Construct a new event to change its type. Use `dataclasses.replace()` only to
change fields within the same event class.

`DeviceEventData`, `Payload`, the payload protocols, and their `*Data` fixtures
are removed. Device models consume typed events instead of generated SDK messages.
