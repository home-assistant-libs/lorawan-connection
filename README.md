# lorawan-connection

[Documentation](https://home-assistant-libs.github.io/lorawan-connection/) ·
[Building a device library](https://home-assistant-libs.github.io/lorawan-connection/patterns/library/)

Backend-neutral LoRaWAN events and device collections for Python 3.12+.
The base package has no runtime dependencies. Install the optional ChirpStack backend
to connect to a server.

A vendor `DeviceCollection` creates supported models and routes incoming events
to them automatically. Applications use device-added callbacks to observe those
models and subscribe to their state updates.

With a connection already created, declare and use the models your library supports:

```python
from lorawan_connection import Device, DeviceCollection, DeviceDescriptor, DeviceEvent


class Sensor(Device):
    vendor_id = 123
    catalog_model_id = "known-model"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        super().__init__(descriptor)
        self.temperature: float | None = None
        self.humidity: float | None = None

    def handle_event(self, event: DeviceEvent) -> None:
        # Decode an event, update model attributes, then call self.notify().
        pass


class Sensors(DeviceCollection[Sensor]):
    DEVICES = (Sensor,)


sensors = Sensors(connection)
unsubscribe = sensors.subscribe_device_added(
    lambda device: print(device.descriptor.name)
)
```

The collection builds its catalog lookup from `DEVICES`. You can also pass classes
at construction: `DeviceCollection(connection, [Sensor])`.
Call `await sensors.async_setup()` to subscribe through the supplied connection.
The collection owns its subscription; the application owns connection cleanup.
The `Device` base supplies identity, `add_update_listener()`, `notify()`, and cleanup.
Models define their own attributes, including multiple measurements or channels.
Listeners take no arguments and read model attributes after a complete update.
See [Connecting to ChirpStack](https://home-assistant-libs.github.io/lorawan-connection/connection/chirpstack/)
to connect the collection to a server.

## Install

```sh
pip install lorawan-connection
```

The [quickstart](https://home-assistant-libs.github.io/lorawan-connection/getting-started/quickstart/)
replays a SenseCAP S2101 capture and prints 21.4 °C and 31.4% humidity.

## Included

- Read-only event and payload `Protocol`s. Generated payloads can pass by reference.
- Immutable descriptors and fixture dataclasses for every supported event payload.
- An in-memory `MockConnection` for device replay, event delivery, and command tests.
- A `Device` base with synchronous update listeners and explicit notifications.
- A generic `DeviceCollection` with inventory replay, model replacement, and retirement.
- Synchronous callback helpers with independent, idempotent unsubscribe functions.
- Typed exports (`py.typed`), a tested SenseCAP example, and Astro/Starlight documentation.

The optional `lorawan_connection.chirpstack` backend supplies inventory and live events.
The backend drops events whose server receipt timestamps are more than five seconds
before each device stream starts. Keep the client and ChirpStack clocks synchronized.
The application owns its connection lifecycle; provisioning, QR parsing, and vendor decoders belong in separate libraries.
The SenseCAP implementation under `examples/` illustrates a separate device library.

Events are live notifications. The package does not persist history, reconnect a
transport, or request replay after a gap. Providers report inventory before activity.
Collections select models from catalog identity, never from names or payload guesses.

## Sending commands

Pass the connection to the collection. Device models encode their
commands and call `async_send_downlink(data=..., f_port=...)`. The method requests a
confirmed downlink and completes after its device ACK. It returns `None` on success.
Use `wait_for_ack=False` to send an unconfirmed command and return after enqueueing.
Callers can bound the wait with `asyncio.timeout()`. Device reports update model
attributes; an ACK confirms delivery, not the resulting device state.

The [Dragino example](https://home-assistant-libs.github.io/lorawan-connection/modelling/overview/#complete-device-example)
models the LT-22222-L using its existing ChirpStack catalog identity.

## Testing device libraries

Use `MockConnection` from `lorawan_connection.mock` in place of a server connection.
Seed it with device descriptors, then call the collection's `async_setup()`.
`connection.emit(event)` delivers events to subscribed collections. Queued commands
are recorded in `connection.downlinks`, keyed by queue ID. Send ACK events explicitly
to test command completion; `connection.disconnect()` simulates connection loss.
See the [testing guide](https://home-assistant-libs.github.io/lorawan-connection/patterns/testing/).

Construct `DeviceEventData` with keyword arguments. Supply `descriptor=...` to derive
`network_id` and `dev_eui`, or pass both identifiers explicitly for an event without
a descriptor. Conflicting explicit identifiers raise `ValueError`.

## Device-library CLI

For a device library named `my_sensors` that exports `Sensors`, put this in
`my_sensors/__main__.py`:

```python
from lorawan_connection.cli_helper import run
from . import Sensors

if __name__ == "__main__":
    run(Sensors.DEVICES)
```

The helper discovers supported devices and prints their state. Models supply catalog
identity and update listeners through the shared `Device` base. The CLI reads public
model attributes and properties.
By default, it discovers applications across all accessible tenants and streams
live updates. Use `--tenant UUID` or repeat `--application UUID` to restrict the
selection. Keys that cannot list tenants require `--tenant`.
Use `--list` to print inventory and exit, or `--json` for machine-readable output.
Unmapped devices from supported vendors produce a warning on stderr, once per
device per run. Each warning includes the device name, DevEUI, vendor ID, and catalog model ID.

```sh
pip install "lorawan-connection[chirpstack]"
python -m my_sensors --server https://chirpstack.example.com:443 --api-key-file /path/to/key
```

See the [CLI guide](https://home-assistant-libs.github.io/lorawan-connection/patterns/cli/)
and [Connecting to ChirpStack](https://home-assistant-libs.github.io/lorawan-connection/connection/chirpstack/).

## Collection subscriptions

Pass a connection to the collection, then call `await devices.async_setup()`.
The collection selects vendor IDs from its registered model classes and receives
existing devices before setup returns. Later events reach models automatically.
`devices.close()` unsubscribes and closes the models without closing the connection.
Listen for a model's removal with `device.add_remove_listener(callback)`. It fires
after removal or replacement closes that model, but not during ordinary shutdown.

`Connection` exposes only `async_subscribe(*, vendor_ids, callback)`,
`on_disconnect(callback)`, and `async_send_downlink(downlink)`.
Applications own connection startup, recovery, and shutdown. The ChirpStack backend
retries transient polling and device-stream failures before reporting connection loss.

For ChirpStack, call `await connection.async_connect()` before setting up collections.
Register disconnect notifications with `connection.on_disconnect(callback)`;
the callback takes no arguments. Use `await devices.async_setup()` to deliver events to models. Several collections can share one connection.

## Home Assistant

The [Home Assistant guide](https://home-assistant-libs.github.io/lorawan-connection/home-assistant/integration/) covers provider subscriptions, discovery, config-entry
lifecycle, and entities that observe library models. The proposed provider API
is not yet part of Home Assistant.

## Development

```sh
uv sync --group compatibility
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov --cov-report=term-missing
uv run python examples/replay.py
cd docs
npm ci
npm run build
```

CI tests Python 3.12, 3.13, and 3.14. Compatibility tests check the payload contracts
against `chirpstack-api==4.19.0`. The optional backend uses the same bindings.
The documentation build validates internal links and produces `llms.txt`.

The repository follows the packaging, documentation, and release approach of
[modbus-connection](https://github.com/home-assistant-libs/modbus-connection).
The source version stays `0.0.0`; the publish workflow sets the version from the
release tag. See [releasing](RELEASING.md) for release instructions.
