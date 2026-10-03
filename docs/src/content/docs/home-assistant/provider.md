---
title: Provider and discovery
description: What the HA LoRaWAN provider owns and what vendor integrations consume.
---

This page documents the [Core POC implementation](https://github.com/balloobbot/core/tree/lorawan-poc/homeassistant/components/lorawan).
Its API is a starting point for HA development, not a released upstream hook.

## Provider ownership

The HA `lorawan` integration owns the external ChirpStack connection, authentication,
selected tenant and applications, catalog device descriptions, and vendor subscriptions.
It uses the optional `lorawan_connection.chirpstack` backend, which combines
device-list polling with per-device event streams.

The initial HA scope assumes devices are provisioned in ChirpStack. Both read-only
and full keys can consume device discovery and events. Future provisioning checks write
permission when the user starts the operation; it should not hide the feature.
Credential failures belong to the provider's reauthentication flow.

## Consumer connection

`lorawan.get_connection(hass, provider_entry_id)` returns a restricted wrapper
implementing the library's `Connection` protocol:

```python
from collections.abc import Callable
from typing import Protocol

from lorawan_connection import DeviceEvent, Downlink, Unsubscribe


class Connection(Protocol):
    async def async_subscribe(
        self,
        *,
        vendor_ids: frozenset[int],
        callback: Callable[[DeviceEvent], None],
    ) -> Unsubscribe: ...

    def on_disconnect(self, callback: Callable[[], None]) -> Unsubscribe: ...

    async def async_send_downlink(self, downlink: Downlink) -> str: ...
```

The wrapper exposes no transport controls, credentials, channel, or public network ID.
Only the provider can start, reconnect, or close the shared connection.

Pass the wrapper to a device collection and call `await devices.async_setup()`.
The collection selects vendor IDs from its registered models. Its subscription
reports existing devices before setup returns, then device changes and activity.
All event types and FPorts are included.

A profile change that moves a device to another vendor sends a removal to the old
vendor and the updated descriptor to the new vendor. Libraries select which models
they support within each vendor.

Register a reload callback with `connection.on_disconnect(callback)`. The callback
takes no arguments. The backend becomes unavailable before notifying listeners.
`get_connection()` and new subscriptions raise `ConnectionUnavailable` while offline.
An old connection stays unusable after the provider reloads; resolve the new one
when setting up the consumer again.

`devices.close()` unsubscribes and closes its models. It leaves the shared connection
open. Remove the disconnect listener when unloading the consumer too.

## Catalog discovery

The POC maps vendor `744` to the `sensecap` integration and creates one discovery
flow per provider. Its vendor table is provisional. A future HA discovery mechanism
must let integrations register the vendor IDs they represent. The POC does not
add a manifest field to upstream HA.

ChirpStack needs an imported global catalog profile for reliable automatic model
identification in the tested version. The `device_id` protobuf field exists, but
creating a custom tenant profile ignored it in the POC. Copying a profile or naming
it after a model is not enough. Custom devices need catalog registration before
they qualify for automatic discovery.

The tested SenseCAP path is:

1. Provision an S2101 in ChirpStack using its recognized catalog profile.
2. Add the LoRaWAN integration and select the tenant and applications.
3. Confirm the discovered SenseCAP integration.
4. The provider's device descriptions creates an S2101 model in the collection.
5. Entities read temperature and humidity after an uplink updates that model.

## ChirpStack findings

The POC tested ChirpStack 4.19.2 with Python bindings 4.19.0:

- A valid tenant-scoped key can receive `UNAUTHENTICATED` from `Tenant.List`.
  Onboarding falls back to a tenant UUID and validates access through tenant/application APIs.
- Inventory has no supported complete change feed in the tested implementation.
  The helper polls the complete device list and diffs descriptors.
- Per-device internal log streams return JSON bodies. The helper parses each body
  once into a generated payload, then wraps it without copying the payload.
- Those streams replay retained entries. The POC filters old entries using stream
  IDs and connection time; this depends on clock alignment and enabled retention.
- Unknown-device activity triggers device-list refresh before delivery. Incomplete
  refreshes preserve the existing device list; bounded buffering limits pending activity.

The main upstream request is one supported subscription for existing devices and
live add/update/remove plus all device activity. No historical replay or durable
resume protocol is needed for this HA use case. Other requests include mDNS
advertisement and distinct permission/authentication errors.
