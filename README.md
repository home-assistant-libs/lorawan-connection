# lorawan-connection

[Documentation](https://home-assistant-libs.github.io/lorawan-connection/) ·
[Building a device library](https://home-assistant-libs.github.io/lorawan-connection/patterns/library/)

Backend-neutral LoRaWAN events and device collections for Python 3.12+.
The base package has no runtime dependencies. Install the optional ChirpStack backend
to connect to a server.

Feed inventory and live events into a vendor `DeviceCollection`. It creates
supported models and reports them through device-added callbacks. Consumers then
observe model state. The caller owns the network connection.

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


sensors = Sensors(network_id="my-network")
stop = sensors.subscribe_device_added(lambda device: print(device.descriptor.name))
# Feed inventory and live events into sensors.handle_event(event).
```

The collection builds its catalog lookup from `DEVICES`. You can also pass classes
at construction: `DeviceCollection([Sensor], network_id="my-network")`.
The `Device` base supplies identity, `add_update_listener()`, `notify()`, and cleanup.
Models define their own attributes, including multiple measurements or channels.
Listeners take no arguments and read model attributes after a complete update.

## Install

```sh
pip install lorawan-connection
```

The [quickstart](https://home-assistant-libs.github.io/lorawan-connection/getting-started/quickstart/)
replays a SenseCAP S2101 capture and prints 21.4 °C and 31.4% humidity.

## Included

- Read-only event and payload `Protocol`s. Generated payloads can pass by reference.
- Immutable descriptors and fixture dataclasses for every supported event payload.
- A `Device` base with synchronous update listeners and explicit notifications.
- A generic `DeviceCollection` with inventory replay, model replacement, and retirement.
- Synchronous callback helpers with independent, idempotent unsubscribe functions.
- Typed exports (`py.typed`), a tested SenseCAP example, and Astro/Starlight documentation.

The optional `lorawan_connection.chirpstack` backend supplies inventory and live events.
The application owns its connection lifecycle; provisioning, QR parsing, and vendor decoders belong in separate libraries.
The SenseCAP implementation under `examples/` illustrates a separate device library.

Events are live notifications. The package does not persist history, reconnect a
transport, or request replay after a gap. Providers report inventory before activity.
Collections select models from catalog identity, never from names or payload guesses.

## Sending commands

ACK waiting is available on `main` and is not yet released on PyPI.

Pass an async `send_downlink` callback to the collection. Device models encode their
commands and call `async_send_downlink(data=..., f_port=...)`. The method requests a
confirmed downlink and waits for its device ACK before returning the queue-item ID.
Use `wait_for_ack=False` to send an unconfirmed command and return after enqueueing.
Callers can bound the wait with `asyncio.timeout()`. Device reports update model
attributes; an ACK confirms delivery, not the resulting device state.

The [Dragino relay example](https://home-assistant-libs.github.io/lorawan-connection/patterns/commands/)
controls both LT-22222-L relays independently. It uses the existing ChirpStack catalog
identity and preserves the other relay when sending a command.

## Device-library CLI

Pass the library's supported model classes to the shared helper:

```python
from lorawan_connection.cli_helper import run
from my_sensors import Sensors

run(Sensors.DEVICES)
```

The helper discovers supported devices and prints their state. Models supply catalog
identity and update listeners through the shared `Device` base. The CLI reads public
model attributes and properties.
By default, it discovers applications across all accessible tenants and streams
live updates. Use `--tenant UUID` or repeat `--application UUID` to restrict the
selection. Keys that cannot list tenants require `--tenant`.
Use `--list` to print inventory and exit, or `--json` for machine-readable output.

```sh
pip install "lorawan-connection[chirpstack]"
python -m my_sensors --server https://chirpstack.example.com:443 --api-key-file /path/to/key
```

See the [CLI guide](https://home-assistant-libs.github.io/lorawan-connection/patterns/cli/)
and [Connecting to ChirpStack](https://home-assistant-libs.github.io/lorawan-connection/connection/chirpstack/).

## Migrating from 0.2

Rename model `product_id` to `catalog_model_id`. Replace state callbacks with
`add_update_listener(callback)`; callbacks take no arguments and read model attributes.
Models inherit `Device`, initialize it with `super().__init__(descriptor)`,
and call `self.notify()` after updating their data.
Declare `DEVICES` on the collection to replace manual lookup dictionaries.

Models now inherit `Device`. Pass the network ID by keyword when constructing
a collection: `Sensors(network_id="my-network")`. Existing `_create_device()`
overrides remain available for custom matching.

## Migrating from 0.3

Inherit `Device` without a state type parameter. Call `super().__init__(descriptor)`
without `state=`. Define your model's data as attributes or properties. An existing
state object can remain a vendor-defined attribute, but the base does not require it.
The CLI's JSON `state` field now contains the model's public attributes and properties.

## Migrating from 0.6 (unreleased)

`Device.async_send_downlink()` now waits for a device ACK by default. Replace its
`confirmed` argument with `wait_for_ack`: waiting automatically requests confirmation.
Use `wait_for_ack=False` for the previous enqueue-only behavior. The low-level
`Downlink.confirmed` field and backend sender still describe the transport request.
Feed ACK events through the collection and close it on disconnect to end pending waits.
Use `asyncio.timeout()` where the caller needs a deadline. Cancelling a wait does
not remove a command already queued on the server.

## Home Assistant

The [Home Assistant guide](https://home-assistant-libs.github.io/lorawan-connection/home-assistant/integration/) covers provider subscriptions, discovery, config-entry
lifecycle, and entities that observe library models. The provider API described
there exists on the linked Core POC branch; it is not yet an upstream HA API.

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
release tag. See [releasing](RELEASING.md) for the first PyPI release setup.
