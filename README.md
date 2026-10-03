# lorawan-connection

[Documentation](https://home-assistant-libs.github.io/lorawan-connection/) ·
[Building a device library](https://home-assistant-libs.github.io/lorawan-connection/patterns/library/) ·
[Home Assistant integration](https://home-assistant-libs.github.io/lorawan-connection/home-assistant/integration/)

Backend-neutral LoRaWAN events and device collections for Python 3.12+.
No runtime dependencies.

Feed inventory and live events into a vendor `DeviceCollection`. It creates
supported models and reports them through device-added callbacks. Consumers then
observe model state. The caller owns the network connection.

```python
from lorawan_connection import DeviceCollection, DeviceDescriptor, DeviceEvent


class Sensor:
    def __init__(self, descriptor: DeviceDescriptor) -> None:
        self.descriptor = descriptor

    def handle_event(self, event: DeviceEvent) -> None:
        # Interpret events and update typed state here.
        pass

    def close(self) -> None:
        # Release model listeners and other model resources here.
        pass


class Sensors(DeviceCollection[Sensor]):
    def _create_device(self, descriptor: DeviceDescriptor) -> Sensor | None:
        if descriptor.vendor_id == 123 and descriptor.catalog_model_id == "known-model":
            return Sensor(descriptor)
        return None


sensors = Sensors("my-network")
stop = sensors.subscribe_device_added(lambda device: print(device.descriptor.name))
# Feed all matching events into sensors.handle_event(event).
# Supported devices appear automatically when their inventory events arrive.
```

## Install

```sh
pip install lorawan-connection
```

For development before the first PyPI publication:

```sh
git clone https://github.com/home-assistant-libs/lorawan-connection.git
cd lorawan-connection
uv sync --group compatibility
uv run python examples/replay.py
```

The replay creates a SenseCAP S2101 model and prints 21.4 °C and 31.4% humidity.
It uses captured bytes and needs no server or Home Assistant installation.

## What ships in 0.1

- Read-only event and payload `Protocol`s. Generated payloads can pass by reference.
- Immutable descriptors and fixture dataclasses for every supported event payload.
- A generic `DeviceCollection` with inventory replay, model replacement, and retirement.
- Synchronous callback helpers with independent, idempotent unsubscribe functions.
- Typed exports (`py.typed`), a tested SenseCAP example, and Astro/Starlight documentation.

ChirpStack API helpers belong to the Home Assistant LoRaWAN integration. This
package has no ChirpStack, protobuf, MQTT, or Home Assistant dependency. It does
not open connections, provision devices, parse QR codes, or provide vendor decoders.
The SenseCAP implementation under `examples/` illustrates a separate device library.

Events are live notifications. The package does not persist history, reconnect a
transport, or request replay after a gap. Providers report inventory before activity.
Collections select models from catalog identity, never from names or payload guesses.

## Library authors and Home Assistant authors

The Building a library guide covers catalog matching, decoding, partial state,
and model subscriptions. It uses no Home Assistant APIs.

The Home Assistant guide covers provider subscriptions, discovery, config-entry
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
against `chirpstack-api==4.19.0`. This is a development dependency only.
The documentation build validates internal links and produces `llms.txt`.

The repository follows the packaging, documentation, and release approach of
[modbus-connection](https://github.com/home-assistant-libs/modbus-connection).
The source version stays `0.0.0`; the publish workflow sets the version from the
release tag. See [releasing](RELEASING.md) for the first PyPI release setup.
