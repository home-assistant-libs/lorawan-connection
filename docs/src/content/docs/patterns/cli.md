---
title: Command-line helper
description: Give users a command that discovers supported devices and prints their state.
---

A device library can expose a CLI by passing its model classes to `run()`.
Put this in the library's `__main__.py`:

```python
from lorawan_connection.cli_helper import run

from . import SenseCapDeviceCollection

if __name__ == "__main__":
    run(SenseCapDeviceCollection.DEVICES)
```

The helper builds the catalog lookup, discovers devices, and feeds events into their
models. It subscribes to each model's state and prints updates. The model owns decoding.

## Model contract

Models inheriting `Device` provide the listener API. Each model supplies:

- `vendor_id` and `catalog_model_id` identify its catalog model.
- Its constructor accepts a `DeviceDescriptor`.
- `descriptor`, `handle_event(event)`, and `close()` provide the usual device lifecycle.
- `add_update_listener(callback)` reports updates and returns an unsubscribe function.

Callbacks take no arguments. On addition and notification, the helper reads public
model attributes and properties. It excludes methods, private attributes, and the
base's identity and lifecycle fields. Keep internal bookkeeping in private attributes;
public properties should return current data without I/O.
The shared collection rejects duplicate catalog identities.

## Run it

Install the optional backend:

```sh
pip install "lorawan-connection[chirpstack]"
```

With a device library named `my_sensors` installed, run:

```sh
python -m my_sensors --server https://chirpstack.example.com:443 \
  --api-key-file /path/to/api-key --list
```

Use `CHIRPSTACK_API_KEY` instead of `--api-key-file` if you keep the key in the environment.
The helper selects the tenant when the key can list exactly one tenant.
Otherwise, pass `--tenant UUID`. Tenant-scoped keys require this argument with the
tested ChirpStack version. All applications in that tenant are selected by default;
repeat `--application UUID` to restrict the selection.

Remove `--list` to print live state changes. Add `--json` for one JSON object per line:

```json
{"type":"state","dev_eui":"0102030405060708","name":"Greenhouse","model":"S2101","state":{"temperature":21.4,"humidity":31.4}}
```

Output types are `added`, `state`, and `removed`. Only supported models appear.
The JSON `state` field contains the model's public data. Dataclass values become JSON
objects. Bytes become hex strings, dates use ISO format, and enums use their values.
Other custom objects use their string representation.

`--list` reads inventory once and opens no event streams. Live mode stops on a
connection failure with exit code 1. Ctrl+C closes models and the connection.
The helper does not reconnect. `--help` works without the optional backend installed.

## Compose a custom command

`add_connection_args(parser)` adds the connection arguments to an `ArgumentParser`.
`await connect_from_args(args)` returns a `ChirpStackConnection` with its tenant and
applications selected. The caller must await `connection.close()`.

`run(models, argv=None)` owns argument parsing, the event loop, output, and cleanup.
Call it from a synchronous script entry point. `argv` is useful when testing a command.

## I/O and callbacks

Network operations are async. API-key file reads run in a worker thread.
Model event handling, update listeners, and CLI printing are synchronous.
Callbacks decode data and update model state; they must not perform network I/O.
