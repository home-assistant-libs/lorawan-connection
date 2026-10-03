---
title: Collection reference
description: Collection lifecycle, callback order, and cleanup behavior.
---

## Device

A structural Protocol. Implement these members in a vendor model:

```python
descriptor: DeviceDescriptor


def handle_event(self, event: DeviceEvent) -> None: ...
def close(self) -> None: ...
```

The collection replaces `descriptor` on metadata updates. `close()` releases model
resources and state listeners. The collection calls it once for each retirement.

## DeviceCollection[DeviceT]

### Construction and attributes

`DeviceCollection(network_id: str)` creates an empty collection for one logical
network. `DeviceT` must satisfy `Device`.

- `network_id: str` identifies the network accepted by this collection.
- `devices: dict[str, DeviceT]` contains current models, keyed by canonical DevEUI.
  Treat it as a read-only view; only the collection changes its contents.

### _create_device(descriptor)

Override this synchronous factory. Return `DeviceT` for a supported descriptor,
or `None` otherwise. The base method raises `NotImplementedError`.
The returned model must use the supplied descriptor. Do not perform network I/O
or feed events back into the collection from the factory.

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
