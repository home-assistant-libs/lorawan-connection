---
title: Provider and discovery
description: What the HA LoRaWAN provider owns and what vendor integrations consume.
---

This page documents the [Core POC implementation](https://github.com/balloobbot/core/tree/lorawan-poc/homeassistant/components/lorawan).
Its API is a starting point for HA development, not a released upstream hook.

## Provider ownership

The HA `lorawan` integration owns the external ChirpStack connection, authentication,
selected tenant and applications, catalog inventory, and vendor subscriptions.
It uses the optional `lorawan_connection.chirpstack` backend, which combines
inventory polling with per-device event streams.

The initial HA scope assumes devices are provisioned in ChirpStack. Both read-only
and full keys can consume inventory and events. Future provisioning checks write
permission when the user starts the operation; it should not hide the feature.
Credential failures belong to the provider's reauthentication flow.

## Vendor subscription

The current integration helper has this signature:

```python
from collections.abc import Callable

from homeassistant.core import HomeAssistant
from lorawan_connection import DeviceEvent, Unsubscribe


async def async_subscribe(
    hass: HomeAssistant,
    provider_entry_id: str,
    vendor_ids: frozenset[int],
    callback: Callable[[DeviceEvent], None],
    on_disconnect: Callable[[], None],
) -> Unsubscribe: ...
```

The subscription covers all devices from the declared vendor IDs in the provider's
selected inventory. It sends existing descriptors as `ADDED` events before returning.
It then delivers inventory changes and all device activity, including all FPorts.

If a profile change moves a device out of a vendor's scope, that vendor receives a
removal. A new matching vendor receives its descriptor. A vendor library still
selects its supported models; subscribing to a vendor does not imply support for
every model from that vendor.

`on_disconnect` signals that this subscription is no longer usable. The provider
marks itself unavailable before invoking it. A new subscription raises
`ConnectionUnavailable` while the provider is disconnected or not loaded.
The returned cleanup function unsubscribes only this consumer; it does not close
the shared connection.

Vendor integrations obtain the collection's connection with
`lorawan.get_connection(hass, provider_entry_id)`. It implements the shared
`Connection` protocol and raises `ConnectionUnavailable` if the provider is not
connected. Pass it to the library collection; the provider owns its lifecycle.
Vendor event delivery still uses `async_subscribe()` above.

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
4. The provider's inventory creates an S2101 model in the collection.
5. Entities read temperature and humidity after an uplink updates that model.

## ChirpStack findings

The POC tested ChirpStack 4.19.2 with Python bindings 4.19.0:

- A valid tenant-scoped key can receive `UNAUTHENTICATED` from `Tenant.List`.
  Onboarding falls back to a tenant UUID and validates access through tenant/application APIs.
- Inventory has no supported complete change feed in the tested implementation.
  The helper polls complete inventory and diffs descriptors.
- Per-device internal log streams return JSON bodies. The helper parses each body
  once into a generated payload, then wraps it without copying the payload.
- Those streams replay retained entries. The POC filters old entries using stream
  IDs and connection time; this depends on clock alignment and enabled retention.
- Unknown-device activity triggers inventory refresh before delivery. Incomplete
  refreshes preserve existing inventory; bounded buffering limits pending activity.

The main upstream request is one supported subscription for current inventory and
live add/update/remove plus all device activity. No historical replay or durable
resume protocol is needed for this HA use case. Other requests include mDNS
advertisement and distinct permission/authentication errors.
