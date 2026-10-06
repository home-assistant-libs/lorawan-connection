---
title: Add a backend
description: Implement server inventory, subscriptions, downlinks, and transport lifecycle behind the shared connection interface.
---

A backend implements `lorawan_connection.Connection` to supply inventory, events,
and downlinks from a server. Keep payload decoding in device libraries and
configuration and reconnection policy in applications.

Use the
[ChirpStack adapter](https://github.com/home-assistant-libs/lorawan-connection/blob/main/src/lorawan_connection/backend/chirpstack.py)
and [TTS adapter](https://github.com/home-assistant-libs/lorawan-connection/blob/main/src/lorawan_connection/backend/tts.py)
as implementation examples.

## Package the adapter

For an adapter included in this repository:

1. Add `src/lorawan_connection/backend/<name>.py`.
2. Put its SDK and transport dependencies in a matching `pyproject.toml` optional extra.
3. Keep `lorawan_connection/__init__.py` and `backend/__init__.py` free of adapter imports.
4. Import the adapter only where an application selects that backend.

Users install the matching extra from PyPI, such as
`pip install "lorawan-connection[tts]"`, then import the adapter explicitly.
An adapter in a separate package can also implement the protocol. No subclass or
backend registration API is required. Its transport dependencies belong to that package.

## Implement the connection contract

Implement these methods. `brands=None` extends the consumer protocol for
applications that need inventory across all brands:

```python
from collections.abc import Callable
from typing import Protocol

from lorawan_connection import DeviceEvent, Downlink, Unsubscribe


class BackendContract(Protocol):
    async def async_subscribe(
        self,
        *,
        brands: frozenset[tuple[str, int | str]] | None,
        callback: Callable[[DeviceEvent], None],
    ) -> Unsubscribe:
        """Deliver matching inventory before returning, then deliver live events."""
        ...

    def on_disconnect(self, callback: Callable[[], None]) -> Unsubscribe:
        """Subscribe to connection loss; return idempotent cleanup."""
        ...

    async def async_send_downlink(self, downlink: Downlink) -> str:
        """Queue once and return the ID used in acknowledgement events."""
        ...
```

The shared `Connection` protocol requires a set of `(stack, brand_id)` pairs.
The included server adapters also accept `brands=None`, meaning every device in
the selected scope. An empty set matches no devices. A subscription includes all
event types and FPorts for its matching devices.

Expose `async_connect()` and `close()` on your concrete adapter to manage its
transport. These methods are not part of the consumer `Connection` protocol.
Follow the included adapters by accepting a caller-supplied `network_id` and
exposing `available` and `error` for connection state.

## Build complete inventory snapshots

Choose a stable `stack` string for the server's catalog namespace. Preserve its
native brand and model IDs. Do not identify models from display names or payloads.

```python
from datetime import UTC, datetime

from lorawan_connection import (
    DeviceDescriptor,
    AddedEvent,
    UplinkEvent,
)

# Replace these values with a device record from your server's registry.
descriptor = DeviceDescriptor(
    network_id="office-network",
    stack="example",
    dev_eui="0201010101010101",
    name="Office sensor",
    application_id="sensors",
    profile_id="",  # Empty when the server has no profile identifier.
    brand_id="acme",
    model_id="temperature-v1",
)

added = AddedEvent(
    descriptor=descriptor,
    received_at=datetime.now(UTC),
)
uplink = UplinkEvent(
    descriptor=descriptor,
    received_at=datetime.now(UTC),
    data=b"\x00\xd6",
    f_port=1,
)
```

Use the same caller-supplied `network_id` on every descriptor and event from the
connection. A DevEUI identifies one device within that connection. Reject ambiguous
duplicate DevEUIs within the selected scope. Define how the adapter handles
registry records without a usable DevEUI.

Read every page and selected application before replacing inventory, so failed reads
cannot appear as deletions. Compare complete snapshots to emit `ADDED`, `UPDATED`, and `REMOVED`.
Retain devices with unknown catalog identity in inventory; leave `brand_id=None`
and `model_id=""` when those fields are absent.

## Order inventory before activity

Register each subscriber before replaying the snapshot. Replay a matching `ADDED`
event for every current device before `async_subscribe()` returns. Start event
streams with the server's supported readiness mechanism and reconcile inventory
across startup. Buffer activity or serialize delivery so a subscriber cannot
receive an uplink before its device descriptor.

For activity from an unknown device, refresh inventory before delivery. Bound any
buffer and define how stale events are discarded. Use server receipt timestamps
when available, and document startup gaps and replay limitations.

If a descriptor's brand changes, notify subscribers that matched its old identity
with `REMOVED`. Deliver the updated descriptor to subscribers matching its new
identity. Later subscriptions must see only the current inventory.

Callbacks run synchronously on the owning event loop. Marshal SDK callbacks from
other threads onto that loop. Use `notify()` to isolate subscriber exceptions.
Return an independent, idempotent unsubscribe callback for every subscription.
Unsubscribing one collection must not close the shared transport or other subscriptions.

## Translate messages and commands

Map server messages to the [event classes](/lorawan-connection/connection/reference/).
Forward raw application bytes in `UplinkEvent`. Copy the relevant scalar fields
from SDK messages into the matching event class.
Never mutate an event after delivery.

`async_send_downlink()` receives the device's DevEUI, FPort, bytes, confirmation
flag, and optional expiry. Validate that the device belongs to the selected scope.
Return the server queue ID, or a correlation ID that the server preserves in its
acknowledgement stream. Use that same value in `AckEvent.queue_item_id`.

Only a device ACK completes a confirmed command successfully. Queue acceptance
and a gateway `TX_ACK` are different events. Translate a correlated negative ACK
or delivery failure to an `AckEvent` with `acknowledged=False` when supported.

Raise `DownlinkError` for rejected writes, unknown devices, unavailable transport,
or unsupported requested options. Keep read-only monitoring available when a write
is rejected. Do not silently drop `expires_at`: either enforce it through the
server or reject it. A caller timeout does not cancel an already queued command.
Do not retry an enqueue automatically when acceptance is uncertain.

## Transport lifecycle

`async_connect()` validates credentials and selected scope, reads inventory, and
starts the required streams or polling tasks. Clean up partially opened resources
if startup fails or is cancelled. Translate transport failures to
`ConnectionUnavailable`. Expose a backend-specific authentication exception so an
application can distinguish invalid credentials from a temporary outage.

On terminal connection loss, set `available=False`, retain the error, and notify
disconnect listeners once. Stop delivering events from the failed transport.
Collections use this notification to fail pending commands while keeping models.
The owner can create a replacement backend and reconcile inventory.

`close()` cancels and awaits owned tasks, removes callbacks, and closes clients.
Make repeated calls harmless. A deliberate close must not request reconnection.
Do not embed an application's retry loop or configuration storage in the adapter.

## Add CLI selection

The shared CLI currently selects `chirpstack` or `tts`; it does not auto-discover
third-party backends. For a new bundled adapter, extend `add_connection_args()` and
`connect_from_args()` in `cli_helper.py`:

- Add its name to `--backend` choices and add only necessary server options.
- Read its credentials from an environment variable or `--api-key-file`.
- Import the adapter inside the selected branch of `connect_from_args()`.
- Report a missing extra with an installation hint.
- Return the scoped adapter; the CLI owns connecting, subscriptions, and closure.

Imports needed only for type annotations belong under `TYPE_CHECKING`.
Check that CLI help and shared imports still work with no optional extras installed.

## Tests

Test the adapter against fake SDK responses and a disposable server. Cover:

- Complete, paginated inventory and failures partway through a snapshot.
- Brand filtering, `brands=None`, descriptor changes, and device removals.
- Inventory-before-uplink ordering and independent unsubscribe callbacks.
- Raw payload conversion and matching queue IDs across enqueue, ACK, and NACK.
- Read-only credentials, unsupported expiry, and uncertain enqueue failures.
- Stream loss, pending commands, setup cancellation, and repeated close.
- Installation with only the backend's own extra, including packaged SDK schemas.

Replace the adapter's SDK client for unit tests. `MockConnection` is for device
library tests and does not exercise an adapter's protocol translation or cleanup.
