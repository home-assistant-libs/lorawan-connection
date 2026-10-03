---
title: Connecting to ChirpStack
description: Read catalog inventory and live device events from an existing ChirpStack server.
---

Install the backend with `pip install "lorawan-connection[chirpstack]"`.
The base package keeps its dependency-free event and collection API.

```python
from lorawan_connection.chirpstack import ChirpStackConnection

connection = ChirpStackConnection(
    endpoint="https://chirpstack.example.com:443",
    api_key=api_key,
    tenant_id=tenant_id,
    application_ids=application_ids,
    network_id="my-network",
)
try:
    stop = await connection.async_subscribe(collection.handle_event, on_disconnect)
    await finished.wait()
    stop()
finally:
    await connection.close()
```

The caller supplies the collection, key, IDs, and disconnect callback.
`network_id` is the caller's stable identifier for this connection's device namespace.
Use `https` for TLS with system trust roots. `http` explicitly selects an unencrypted
connection. TLS errors never cause a plaintext fallback.

Set `tenant_id=""` to discover applications across all accessible tenants. Supply a
tenant UUID to restrict discovery to that tenant, or when the API key cannot list
tenants. `application_ids` selects which applications supply inventory and events.

## Inventory and events

`async_subscribe(callback, on_disconnect)` delivers current devices as `ADDED` events
before returning. It then delivers inventory changes and device activity.
There is one subscription per connection; applications can distribute events to
several consumers. Call `close()` after unsubscribing to release the connection.

The backend polls inventory every 30 seconds and opens an internal ChirpStack event
stream for each device. It resolves catalog profiles into model and vendor identities.
Custom profiles without catalog identity remain in inventory, with no recognized model.
An incomplete inventory read preserves the previous inventory.

Generated protobuf payloads pass through the event envelope by reference. The internal
stream returns JSON, so each payload must first be parsed into a generated message.
Newly observed devices receive an inventory event before their activity is delivered.

The internal API is a compatibility dependency, tested against ChirpStack 4.19.2 and
Python bindings 4.19.0. It requires device event retention to be enabled. Retained events
older than connection creation are skipped using stream timestamps, which depend on
server/client clock alignment. Events missed during a disconnect are not recovered.

## API reference

`ChirpStackConnection(endpoint, api_key, tenant_id, application_ids, network_id, *,
poll_interval=30, channel=None)` owns its channel, including an injected test channel.

| Method or attribute | Behavior |
| --- | --- |
| `await tenants()` | Return `{tenant_uuid: name}`. A scoped key may be denied. |
| `await applications()` | Return `{application_uuid: name}` for the selected tenant, or all accessible tenants when `tenant_id` is empty. |
| `await inventory()` | Read descriptors without starting streams or updating the subscription inventory. |
| `await async_subscribe(callback, on_disconnect)` | Start inventory polling and live events; return a synchronous unsubscribe function. |
| `await async_send_downlink(downlink)` | Validate device scope and enqueue application bytes; return the queue-item UUID. |
| `await refresh()` | Refresh a running subscription's inventory. |
| `await close()` | Stop tasks, await cleanup, and close the channel. |
| `devices` | Current subscription inventory, keyed by DevEUI. |
| `available` | Whether the subscription can deliver events. |

Callbacks run synchronously. `on_disconnect(error)` runs after `available` becomes
false. Create a new connection to recover. `AuthenticationError` reports rejected
credentials or access scope; `ConnectionUnavailable` reports other subscription failures.
ChirpStack can return the same authentication error for an invalid key and missing
permission. Discovery methods retain gRPC errors so callers can offer a tenant-ID fallback.

Provision devices in ChirpStack. This backend reads inventory and events and sends downlinks; it does not
configure gateways or provision devices.

## Write access

Downlinks use the public `DeviceService.Enqueue` gRPC method. The connection must
have an active subscription, and the device must belong to its selected inventory.
Before enqueueing, the backend reads the device to check its current application.
Use a tenant API key with write access for commands. A read-only key still supports
inventory and live events; attempted writes raise `DownlinkError`.

`Downlink(dev_eui, f_port, data, confirmed=False, expires_at=None)` contains plaintext
application bytes. FPort must be 1–223. An optional expiry is a timezone-aware datetime.
ChirpStack owns encryption, frame counters, scheduling, and expiry. The helper never
flushes existing commands or retries an ambiguous RPC result.

A returned queue ID means accepted, a `TX_ACK` means transmitted, and a positive
`ACK` means the device acknowledged a confirmed downlink. Models interpret device
reports to determine whether the requested action took effect.
