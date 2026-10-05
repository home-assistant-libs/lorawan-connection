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

The helper connects a `DeviceCollection` to the selected server. The collection creates
supported models and routes events to them. The helper subscribes to each model's
state and prints updates. The model owns decoding.

## Run it

Install the optional backend:

```sh
pip install "lorawan-connection[chirpstack]"
```

With a device library named `my_sensors` installed, run:

```sh
python -m my_sensors --backend chirpstack --server https://chirpstack.example.com:443 \
  --api-key-file /path/to/api-key
```

`--backend` selects `chirpstack` (the default) or `tts`. The helper imports the selected adapter when connecting.
An unavailable extra produces an installation command. An unknown backend is
rejected before opening a connection.

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
If a device has a supported brand ID but no matching model, the helper warns on
stderr once per device per run. The warning includes its name, DevEUI, brand ID,
and model ID. This also applies to `--list`; `--json` output stays on stdout.

The helper prints public model attributes and properties, excluding methods,
private attributes, and the base class's identity and lifecycle fields. Properties
should return current data without I/O. In JSON output, these values appear in
`state`. Dataclass values become JSON objects. Bytes become hex strings, dates use
ISO format, and enums use their values. Other custom objects use their string
representation.

Add `--list` to read the device list once and exit. Live mode stops on a
connection failure with exit code 1. Ctrl+C closes models and the connection.
The helper does not reconnect. `--help` works without the optional backend installed.

## The Things Stack

Install `lorawan-connection[tts]` and select `--backend tts`. Supply at least one
`--application` ID. Use `TTS_API_KEY` or `--api-key-file` for its application key.
Set `--identity-server` when the Identity Server differs from `--server`, which
selects the Application Server. `--tenant` is specific to ChirpStack. See the
[TTS connection guide](/lorawan-connection/connection/tts/) for rights and examples.
