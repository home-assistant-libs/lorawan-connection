---
title: Event reference
description: Exact fields for descriptors, event envelopes, and payload contracts.
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

## DeviceEvent and DeviceEventData

`DeviceEvent` is a read-only protocol; `DeviceEventData` is its frozen, slotted dataclass implementation.

| Field | Type | Dataclass default |
| --- | --- | --- |
| `network_id` | `str` | From `descriptor`; otherwise required |
| `dev_eui` | `str` | From `descriptor`; otherwise required |
| `type` | `EventType` | Required |
| `received_at` | `datetime` | Required |
| `descriptor` | `DeviceDescriptor \| None` | `None` |
| `data` | `Payload \| None` | `None` |

Pass constructor arguments by keyword. When `descriptor` is supplied, the event
derives `network_id` and `dev_eui` from it. Explicit identifiers must match that
descriptor; conflicting values raise `ValueError`. DevEUI comparisons ignore
colons and letter case. Without a descriptor, both identifiers are required.

Providers use timezone-aware timestamps and canonical DevEUIs. The envelope
retains its inputs without copying or runtime payload validation. Supply a
descriptor for `ADDED` and `UPDATED`. `REMOVED` can omit it. Activity must carry
the payload that corresponds to its event type.

## EventType and Payload

`EventType` is a `StrEnum`. `Payload` is the union of the seven activity Protocols.
The Protocols are not runtime-checkable. Dispatch on the event type, then use
`typing.cast` for static narrowing when needed.

| Member | String | Payload Protocol | Fixture |
| --- | --- | --- | --- |
| `ADDED` | `added` | None | Descriptor in envelope |
| `UPDATED` | `updated` | None | Descriptor in envelope |
| `REMOVED` | `removed` | None | Optional descriptor |
| `UPLINK` | `up` | `Uplink` | `UplinkData` |
| `JOIN` | `join` | `Join` | `JoinData` |
| `STATUS` | `status` | `Status` | `StatusData` |
| `ACK` | `ack` | `Ack` | `AckData` |
| `TX_ACK` | `txack` | `TxAck` | `TxAckData` |
| `LOG` | `log` | `Log` | `LogData` |
| `LOCATION` | `location` | `Location` | `LocationData` |

### Uplink

`data: bytes` contains raw application bytes. `f_port: int` contains the application
port. `UplinkData(data: bytes, f_port: int = 1)` implements this contract.
The provider forwards all ports. The device library decides which it understands.

### Join

`dev_addr: str` contains the joined device address. Fixture: `JoinData(dev_addr)`.

### Status

Fields: `margin: int`, `external_power_source: bool`,
`battery_level_unavailable: bool`, and `battery_level: float`.

`StatusData(margin=0, external_power_source=False,
battery_level_unavailable=True, battery_level=0)` defaults to unknown battery.
A zero battery value with `battery_level_unavailable=False` is a known zero.
Interpret the two flags before using a battery reading. `battery_level` is a
percentage when available; it is not the raw LoRaWAN MAC battery byte.

### Ack

`queue_item_id: str` and `acknowledged: bool` describe a device acknowledgement.
Fixture: `AckData(queue_item_id, acknowledged)`.

### TxAck

`gateway_id: str` and `downlink_id: int` describe a gateway transmission
acknowledgement. This does not establish device receipt.
Fixture: `TxAckData(gateway_id, downlink_id)`.

### Log

`description: str`, `level: int`, and `code: int` describe a backend log message.
Fixture: `LogData(description, level, code)`. Numeric level and code values retain
the backend's enums. The library does not define a cross-backend enum mapping.

### Location and Coordinates

`Location.location` is a `Coordinates` object with `latitude: float`,
`longitude: float`, and `altitude: float`. Latitude and longitude are degrees;
altitude is meters. Fixtures: `CoordinatesData(latitude, longitude, altitude)`
and `LocationData(location)`. The coordinates object is borrowed by reference.

Fixture dataclasses do not validate radio ranges, checksums, or payload authenticity.
Validate wire formats in the vendor decoder.
