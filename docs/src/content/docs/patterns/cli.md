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

The helper connects a `DeviceCollection` to ChirpStack. The collection creates
supported models and routes events to them. The helper subscribes to each model's
state and prints updates. The model owns decoding.

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
  --api-key-file /path/to/api-key
```

Use `CHIRPSTACK_API_KEY` instead of `--api-key-file` if you keep the key in the environment.
The helper discovers applications across all tenants accessible to the API key
at startup. Pass `--tenant UUID` to restrict it to one tenant, or repeat
`--application UUID` to select specific applications. Keys that cannot list tenants
require `--tenant`; this includes tenant-scoped keys with the tested ChirpStack version.

The command prints discovered devices and stays connected to print live state
changes. Add `--json` for one JSON object per line:

```json
{"type":"state","dev_eui":"0102030405060708","name":"Greenhouse","model":"S2101","state":{"temperature":21.4,"humidity":31.4}}
```

Output types are `added`, `state`, and `removed`. Only supported models appear.
If a device has a supported vendor ID but no matching model, the helper warns on
stderr once per device per run. The warning includes its name, DevEUI, vendor ID,
and catalog model ID. This also applies to `--list`; `--json` output stays on stdout.
The JSON `state` field contains the model's public data. Dataclass values become JSON
objects. Bytes become hex strings, dates use ISO format, and enums use their values.
Other custom objects use their string representation.

Add `--list` to read inventory once and exit. Live mode stops on a
connection failure with exit code 1. Ctrl+C closes models and the connection.
The helper does not reconnect. `--help` works without the optional backend installed.

## Compose a custom command

`add_connection_args(parser)` adds the connection arguments to an `ArgumentParser`.
`await connect_from_args(args)` returns a `ChirpStackConnection` with its application
selection and optional tenant filter. The caller must await `connection.close()`.

`run(models, argv=None)` owns argument parsing, the event loop, output, and cleanup.
Call it from a synchronous script entry point. `argv` is useful when testing a command.

## I/O and callbacks

Network operations are async. API-key file reads run in a worker thread.
Model event handling, update listeners, and CLI printing are synchronous.
Callbacks decode data and update model state; they must not perform network I/O.
