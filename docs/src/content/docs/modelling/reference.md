---
title: Collection reference
description: Collection lifecycle, listener order, and cleanup behavior.
---

## Changes in 0.13

Subscription parameters are named `listener`. Replace `callback=` with `listener=`
when calling `Connection.async_subscribe()`, `Connection.on_disconnect()`,
`DeviceCollection.subscribe_device_added()`, `DeviceCollection.subscribe_device_removed()`,
and `subscribe()`. Positional calls continue to work.

Custom backends must use the new parameter names to implement `Connection`.
The ChirpStack, TTS, and mock connections use the same signatures.

## Device

Subclass `Device`, declare `identifiers: Mapping[str, tuple[int | str, str]]`, and
override `handle_event(event)` to decode application data. Each identifier maps a stack to its `(brand_id, model_id)`.

Call `super().__init__(descriptor)` to initialize identity and listeners, then define
the model's data attributes. The collection replaces `descriptor` on metadata updates.

`Device` can also be instantiated directly as a generic model. Its `identifiers`
mapping is empty and its `handle_event()` does nothing.

### Status properties

The collection delivers events through an internal receive method. It stores
`StatusEvent` and resolves acknowledgements before calling `handle_event()`.
Overrides do not need to call `super().handle_event()`.

| Property | Type | Meaning |
| --- | --- | --- |
| `latest_status` | `StatusEvent \| None` | Last received status event, including its timestamp. |
| `battery_level` | `float \| None` | Battery percentage; `None` when unavailable or externally powered. |
| `external_power_source` | `bool \| None` | External-power flag; `None` before the first status report. |
| `downlink_margin` | `int \| None` | Device-reported SNR in dB for the received status request. |

These read-only properties describe LoRaWAN MAC status. Application payload
readings belong to the vendor model. A known zero battery level remains `0`.
An unavailable reading clears the exposed battery level to `None`.

Each received status replaces the previous report and schedules an update
notification. Other events leave status unchanged. Reports are stored in delivery
order; use `latest_status.received_at` to inspect their age. State lasts for the
model's lifetime and is not persisted across replacement or collection recreation.

### add_update_listener(listener) → Unsubscribe

Register a synchronous `Callable[[], None]`. No initial callback is sent. Read
model attributes directly for initial values. Each registration is independent;
its unsubscribe function is idempotent. Registration after close raises `RuntimeError`.

### add_remove_listener(listener) → Unsubscribe

Register a synchronous callback with no arguments. It runs once when the collection
removes or replaces this model, after closing it. There is no initial callback.
The returned unsubscribe function is idempotent. Registration after close raises
`RuntimeError`. Listener failures are logged without blocking other listeners.

Calling `device.close()` or `collection.close()` clears these listeners without
reporting removal. Disconnect and shutdown are separate from device removal.

### notify() → None

Call current update listeners with no arguments. This method does not compare or
modify state. Notify after committing a complete model update. Removed listeners
are skipped, new listeners wait for the next notification, and failures are logged
without stopping other listeners. Closed devices ignore notifications.

During internal event delivery, notification calls are combined and delivered
after `handle_event()` returns. A status report notifies automatically, even if the
model handler does nothing. If the handler raises, the stored status still notifies
listeners and the exception propagates.

### async_send_downlink(*, data, f_port, wait_for_ack=True, expires_at=None) → None

Queue application bytes for this model's DevEUI using its collection's sender.
By default, request a confirmed downlink and complete after a positive device ACK.
Returns `None` on success. The collection correlates ACKs before forwarding them to the
model's `handle_event()`. The model does not need to call a base event handler.

Set `wait_for_ack=False` to send an unconfirmed downlink and return when queued.
Use `asyncio.timeout()` to bound the operation. Cancellation ends the wait without removing a queued command.
`expires_at` is a separate server queue expiry.

A negative ACK, closed model, or missing sender raises `DownlinkError`. Closing
the device ends pending ACK waits. Sender errors and cancellation propagate without
retries. This method does not change reported model attributes.

### close() → None

Set `closed` to `True`, clear listeners, and fail pending ACK waits. Repeated calls are harmless. Models with
additional resources can override this method and call `super().close()`.

## DeviceCollection[DeviceT]

### Construction and attributes

`DeviceCollection(connection, models=None)` creates a collection for one logical
network. `models` is a sequence of model classes; when omitted, the collection uses
its `DEVICES` declaration.

With neither explicit models nor a `DEVICES` declaration, the collection subscribes
to all devices and creates generic `Device` instances. With model classes, it
subscribes to their vendors and creates only matching models. An explicit empty
sequence selects no devices, including when it overrides a subclass's `DEVICES`.

- `DEVICES` is a sequence of supported model classes, usually declared as a tuple.
- `connection` implements the `Connection` protocol. The collection uses it to
  subscribe to events and send commands.
- `devices: dict[str, DeviceT]` contains current models, keyed by canonical DevEUI.
  Treat it as a read-only view; only the collection changes its contents.

The collection copies the registry at construction. Duplicate
`(stack, brand_id, model_id)` pairs raise `ValueError`. A subclass inherits `DEVICES`
unless it replaces that declaration. Explicit constructor models override it.
Registered models inherit `Device` and accept a descriptor as their constructor argument.

### Connection protocol

The backend-neutral `Connection` protocol exposes:

- `async_subscribe(*, brands, listener) -> Unsubscribe` takes a `frozenset[tuple[str, int | str]]` or `None` and reports matching existing
  devices before returning, then live events. `None` selects all devices; an empty set selects none. Arguments are keyword-only.
- `on_disconnect(listener) -> Unsubscribe` registers a notification callback with no arguments.
- `async_send_downlink(downlink: Downlink) -> str` queues a command and returns its
  queue ID for internal ACK correlation.

The connection owner controls startup, recovery, and shutdown. Those operations
are outside this protocol. Unavailable subscriptions raise `ConnectionUnavailable`.
Read-only connections raise `DownlinkError` on attempted writes.

### async_setup() → None

Subscribe to the registered stack and brand pairs, or all devices for a generic collection. Existing
models are ready when setup returns. Later events reach `handle_event()` automatically.
Setup is allowed once per collection. Calling it again or after close raises `RuntimeError`.
A failed or cancelled setup closes any models already created and propagates the error.
The collection listens for connection loss and fails pending command waits with
`DownlinkError`. Models remain open. An application can retain the collection when
its connection supports recovery, or close it when ending the session.

### _create_device(descriptor)

The default factory matches `(stack, brand_id, model_id)` and constructs the
registered class with the descriptor. An unknown identity returns `None`.
A generic collection constructs `Device` for every descriptor.
Override this method for custom matching. Return a model with the supplied descriptor,
or `None`. Do not perform I/O or feed events back into the collection from the factory.

### handle_event(event: DeviceEvent) → None

- Closed collections ignore events. The connection scopes events to its network.
- Event DevEUIs are compared after removing colons and lowercasing.
- `ADDED` and `UPDATED` carry the descriptor used to construct a generic device or select a registered model.
- Either `ADDED` or `UPDATED` can create a model. Repeated descriptions do not create duplicates.
- A changed `(stack, brand_id, model_id)` closes and removes the previous model before calling the factory.
- An unchanged identity updates `device.descriptor` in place.
- New models are stored, then device-added callbacks run, then the model receives the event.
- `REMOVED` retires the model without forwarding that event to its `handle_event` method.
- Other events go to the existing model. Unknown-device activity is ignored.

Factory and `handle_event` exceptions propagate to the caller. A provider can
isolate each consumer's callback. Models should handle malformed vendor payloads
without changing state.

### subscribe_device_added(listener) → Unsubscribe

The callback takes one `DeviceT` and returns `None`. It immediately receives all
current models, then future additions. Replay is synchronous, before this method
returns. A model retired during replay is skipped.

### subscribe_device_removed(listener) → Unsubscribe

The callback takes one `DeviceT` and returns `None`. It receives future retirements,
after the model is removed and `close()` is called. There is no initial replay.
This collection-level callback also reports models retired during collection shutdown.
Use `device.add_remove_listener()` to observe only removal or replacement of one model.

Both methods raise `RuntimeError` after the collection is closed. Their returned
unsubscribe functions are idempotent. Callback exceptions are logged and isolated.
Callbacks must be synchronous and should only observe models or manage listeners.
They can close the collection; do not recursively inject device changes.

### close() → None

Unsubscribe, retire all models, and clear listeners, leaving the shared connection
open. Repeated calls and subsequent events do nothing. Cleanup failures are logged;
other models still close and removal callbacks still run.

## Callback helpers

### Unsubscribe

An alias for `Callable[[], None]`.

### subscribe(listeners, listener) → Unsubscribe

`listeners` is a `list[Callable[[T], None]]`. Registers one subscription without
replaying any state. Each registration is independent, including repeated use of
the same callback. Calling its unsubscribe function repeatedly is harmless.

### notify(listeners, value) → None

Deliver synchronously to the listeners present at dispatch start. A subscription
removed before its turn is skipped. A new subscription waits until the next
notification. Exceptions are logged and do not stop other listeners.

The helpers do not await returned values. Use ordinary functions, not async ones.
