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


class Sensor(Device[float | None]):
    vendor_id = 123
    catalog_model_id = "known-model"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        super().__init__(descriptor, state=None)

    def handle_event(self, event: DeviceEvent) -> None:
        # Decode an event, assign self.state, then call self.notify().
        pass


class Sensors(DeviceCollection[Sensor]):
    DEVICES = (Sensor,)


sensors = Sensors(network_id="my-network")
stop = sensors.subscribe_device_added(lambda device: print(device.descriptor.name))
# Feed inventory and live events into sensors.handle_event(event).
```

The collection builds its catalog lookup from `DEVICES`. You can also pass classes
at construction: `DeviceCollection([Sensor], network_id="my-network")`.
The `Device` base supplies typed state, `add_update_listener()`, `notify()`, and cleanup.
Listeners take no arguments and read the model's state after a complete update.

## Install

```sh
pip install lorawan-connection
```

The [quickstart](https://home-assistant-libs.github.io/lorawan-connection/getting-started/quickstart/)
replays a SenseCAP S2101 capture and prints 21.4 °C and 31.4% humidity.

## Included

- Read-only event and payload `Protocol`s. Generated payloads can pass by reference.
- Immutable descriptors and fixture dataclasses for every supported event payload.
- A typed `Device` base with synchronous update listeners and explicit notifications.
- A generic `DeviceCollection` with inventory replay, model replacement, and retirement.
- Synchronous callback helpers with independent, idempotent unsubscribe functions.
- Typed exports (`py.typed`), a tested SenseCAP example, and Astro/Starlight documentation.

The optional `lorawan_connection.chirpstack` backend supplies inventory and live events.
The application owns its connection lifecycle; provisioning, QR parsing, and vendor decoders belong in separate libraries.
The SenseCAP implementation under `examples/` illustrates a separate device library.

Events are live notifications. The package does not persist history, reconnect a
transport, or request replay after a gap. Providers report inventory before activity.
Collections select models from catalog identity, never from names or payload guesses.

## Device-library CLI

Pass the library's supported model classes to the shared helper:

```python
from lorawan_connection.cli_helper import run
from my_sensors import Sensors

run(Sensors.DEVICES)
```

The helper discovers supported devices and prints their state. Models supply catalog
identity, state, and update listeners through the shared `Device` base.
Use `--list` for inventory or `--json` for machine-readable output.

```sh
pip install "lorawan-connection[chirpstack]"
python -m my_sensors --server https://chirpstack.example.com:443 --api-key-file /path/to/key
```

See the [CLI guide](https://home-assistant-libs.github.io/lorawan-connection/patterns/cli/)
and [ChirpStack backend](https://home-assistant-libs.github.io/lorawan-connection/connection/chirpstack/).

## Migrating from 0.2

Rename model `product_id` to `catalog_model_id`. Replace state callbacks with
`add_update_listener(callback)`; callbacks take no arguments and read `device.state`.
Models can inherit `Device[StateT]`, initialize it with `super().__init__(descriptor,
state=initial_state)`, and call `self.notify()` after updating state.
Declare `DEVICES` on the collection to replace manual lookup dictionaries.

Models now inherit `Device[StateT]`. Pass the network ID by keyword when constructing
a collection: `Sensors(network_id="my-network")`. Existing `_create_device()`
overrides remain available for custom matching.

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
