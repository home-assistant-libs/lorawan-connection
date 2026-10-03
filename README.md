# lorawan-connection

[Documentation](https://home-assistant-libs.github.io/lorawan-connection/) ·
[Building a device library](https://home-assistant-libs.github.io/lorawan-connection/patterns/library/)

Backend-neutral LoRaWAN events and device collections for Python 3.12+.
No runtime dependencies.

Feed inventory and live events into a vendor `DeviceCollection`. It creates
supported models and reports them through device-added callbacks. Consumers then
observe model state. The caller owns the network connection.

```python
from lorawan_connection import DeviceCollection, DeviceDescriptor, DeviceEvent


class Sensor:
    vendor_id = 123
    product_id = "known-model"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        self.descriptor = descriptor

    def handle_event(self, event: DeviceEvent) -> None:
        # Interpret events and update typed state here.
        pass

    def close(self) -> None:
        # Release model listeners and other model resources here.
        pass


SUPPORTED_MODELS = [Sensor]
DEVICE_MODELS: dict[tuple[int | None, str], type[Sensor]] = {
    (model.vendor_id, model.product_id): model for model in SUPPORTED_MODELS
}


class Sensors(DeviceCollection[Sensor]):
    def _create_device(self, descriptor: DeviceDescriptor) -> Sensor | None:
        model_class = DEVICE_MODELS.get(
            (descriptor.vendor_id, descriptor.catalog_model_id)
        )
        return model_class(descriptor) if model_class is not None else None


sensors = Sensors("my-network")
stop = sensors.subscribe_device_added(lambda device: print(device.descriptor.name))
# Feed all matching events into sensors.handle_event(event).
# Supported devices appear automatically when their inventory events arrive.
```

## Install

```sh
pip install lorawan-connection
```

The [quickstart](https://home-assistant-libs.github.io/lorawan-connection/getting-started/quickstart/)
replays a SenseCAP S2101 capture and prints 21.4 °C and 31.4% humidity.

## What ships in 0.1

- Read-only event and payload `Protocol`s. Generated payloads can pass by reference.
- Immutable descriptors and fixture dataclasses for every supported event payload.
- A generic `DeviceCollection` with inventory replay, model replacement, and retirement.
- Synchronous callback helpers with independent, idempotent unsubscribe functions.
- Typed exports (`py.typed`), a tested SenseCAP example, and Astro/Starlight documentation.

The application supplies the network connection. This package handles events and
collections; provisioning, QR parsing, and vendor decoders belong in separate libraries.
The SenseCAP implementation under `examples/` illustrates a separate device library.

Events are live notifications. The package does not persist history, reconnect a
transport, or request replay after a gap. Providers report inventory before activity.
Collections select models from catalog identity, never from names or payload guesses.

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
against `chirpstack-api==4.19.0`. This is a development dependency only.
The documentation build validates internal links and produces `llms.txt`.

The repository follows the packaging, documentation, and release approach of
[modbus-connection](https://github.com/home-assistant-libs/modbus-connection).
The source version stays `0.0.0`; the publish workflow sets the version from the
release tag. See [releasing](RELEASING.md) for the first PyPI release setup.
