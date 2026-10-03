---
title: Collection reference
description: Collection lifecycle, callback order, and cleanup behavior.
---

## Device

An abstract model base. Declare class attributes `vendor_id: int` and
`catalog_model_id: str`, then implement `handle_event(event)`.

`Device(descriptor)` initializes `descriptor` and listeners. Model constructors
accept a descriptor and call `super().__init__(descriptor)`. The model defines its
own data attributes; the base imposes no measurement schema or state container.
The collection replaces `descriptor` on metadata updates.

### add_update_listener(listener) → Unsubscribe

Register a synchronous `Callable[[], None]`. No initial callback is sent. Read
model attributes directly for initial values. Each registration is independent;
its unsubscribe function is idempotent. Registration after close raises `RuntimeError`.

### notify() → None

Call current update listeners with no arguments. This method does not compare or
modify state. Notify after committing a complete model update. Removed listeners
are skipped, new listeners wait for the next notification, and failures are logged
without stopping other listeners. Closed devices ignore notifications.

### async_send_downlink(*, data, f_port, wait_for_ack=True, expires_at=None) → None

Queue application bytes for this model's DevEUI using its collection's sender.
By default, request a confirmed downlink and complete after a positive device ACK.
Returns `None` on success. The collection correlates ACKs before forwarding them to the
model's `handle_event()`. The model does not need to call a base event handler.

Set `wait_for_ack=False` to send an unconfirmed downlink and return when queued.
There is no separate `confirmed` or timeout argument. Use `asyncio.timeout()` to
bound the operation. Cancellation ends the wait without removing a queued command.
`expires_at` is a separate server queue expiry.

A negative ACK, closed model, or missing sender raises `DownlinkError`. Closing
the device ends pending ACK waits. Sender errors and cancellation propagate without
retries. This method does not change reported model attributes.

### close() → None

Set `closed` to `True`, clear listeners, and fail pending ACK waits. Repeated calls are harmless. Models with
additional resources can override this method and call `super().close()`.

## DeviceCollection[DeviceT]

### Construction and attributes

`DeviceCollection(models=None, *, network_id, send_downlink=None)` creates a collection for one logical
network. `models` is a sequence of model classes; when omitted, the collection uses
its `DEVICES` declaration. An explicit empty sequence accepts no models.

- `DEVICES` is a sequence of supported model classes, usually declared as a tuple.
- `send_downlink` is an optional `Callable[[Downlink], Awaitable[str]]`. The collection
  attaches it to every model before notifying device-added listeners.
- `network_id: str` identifies the network accepted by this collection.
- `devices: dict[str, DeviceT]` contains current models, keyed by canonical DevEUI.
  Treat it as a read-only view; only the collection changes its contents.

The collection copies the registry at construction. Duplicate
`(vendor_id, catalog_model_id)` pairs raise `ValueError`. A subclass inherits `DEVICES`
unless it replaces that declaration. Explicit constructor models override it.
Pass `network_id` by keyword. Registered models inherit `Device` and accept a
descriptor as their constructor argument.

### _create_device(descriptor)

The default factory matches `(vendor_id, catalog_model_id)` and constructs the
registered class with the descriptor. An unknown identity returns `None`.
Override this method for custom matching. Return a model with the supplied descriptor,
or `None`. Do not perform I/O or feed events back into the collection from the factory.

### handle_event(event: DeviceEvent) → None

- Closed collections and events for another network are ignored.
- Event DevEUIs are compared after removing colons and lowercasing.
- `ADDED` and `UPDATED` need a matching descriptor. Missing or mismatched descriptors are ignored.
- Either inventory type can create a model. Duplicate inventory does not create duplicates.
- A changed `(vendor_id, catalog_model_id)` closes and removes the previous model before calling the factory.
- An unchanged identity updates `device.descriptor` in place.
- New models are stored, then device-added callbacks run, then the model receives the event.
- `REMOVED` retires the model without forwarding that event to its `handle_event` method.
- Other events go to the existing model. Unknown-device activity is ignored.

Factory and `handle_event` exceptions propagate to the caller. A provider can
isolate each consumer's callback. Models should handle malformed vendor payloads
without changing state.

### subscribe_device_added(callback) → Unsubscribe

The callback takes one `DeviceT` and returns `None`. It immediately receives all
current models, then future additions. Replay is synchronous, before this method
returns. A model retired during replay is skipped.

### subscribe_device_removed(callback) → Unsubscribe

The callback takes one `DeviceT` and returns `None`. It receives future retirements,
after the model is removed and `close()` is called. There is no initial replay.

Both methods raise `RuntimeError` after the collection is closed. Their returned
unsubscribe functions are idempotent. Callback exceptions are logged and isolated.
Callbacks must be synchronous and should only observe models or manage listeners.
They can close the collection; do not recursively inject inventory changes.

### close() → None

Permanently close the collection, retire all models, and clear all listeners.
Repeated calls do nothing. Cleanup exceptions from one model are logged; other
models still close and removal callbacks still run. Subsequent events are ignored.

## Callback helpers

### Unsubscribe

An alias for `Callable[[], None]`.

### subscribe(listeners, callback) → Unsubscribe

`listeners` is a `list[Callable[[T], None]]`. Registers one subscription without
replaying any state. Each registration is independent, including repeated use of
the same callback. Calling its unsubscribe function repeatedly is harmless.

### notify(listeners, value) → None

Deliver synchronously to the listeners present at dispatch start. A subscription
removed before its turn is skipped. A new subscription waits until the next
notification. Exceptions are logged and do not stop other listeners.

The helpers do not await returned values. Use ordinary functions, not async ones.
