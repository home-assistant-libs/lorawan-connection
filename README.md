# lorawan-connection

[Documentation](https://home-assistant-libs.github.io/lorawan-connection/) ·
[Building a device library](https://home-assistant-libs.github.io/lorawan-connection/patterns/library/)

Backend-neutral LoRaWAN events and device collections for Python 3.12+.
The base package has no runtime dependencies. Install the optional ChirpStack or
The Things Stack backend to connect to a server.

A vendor `DeviceCollection` creates supported models and routes incoming events
to them automatically. Applications use device-added callbacks to observe those
models and subscribe to their state updates.

With a connection already created, declare and use the models your library supports:

```python
from lorawan_connection import Device, DeviceCollection, DeviceDescriptor, DeviceEvent


class Sensor(Device):
    identifiers = {
        "chirpstack": (123, "known-model"),
        "tts": ("example-brand", "known-model"),
    }

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

Each model declares native `(brand_id, model_id)` pairs by stack. The collection
selects models from `DEVICES`; alternatively pass them to
`DeviceCollection(connection, [Sensor])`.

Call `await sensors.async_setup()` to receive inventory and live events.
Applications observe models through `add_update_listener()`. Models decode readings,
update their attributes, then call `notify()`. Closing the collection removes
subscriptions and closes models, leaving the connection open.

The base device stores each `StatusEvent` before calling the model's `handle_event()`.
Read `latest_status`, `battery_level`, `external_power_source`, and `downlink_margin`
without implementing a status handler. These properties describe LoRaWAN MAC
status; device-specific battery readings stay in the vendor model.

To list all devices without selecting model classes:

```python
devices = DeviceCollection(connection)
await devices.async_setup()
```

This collection creates a generic `Device` for every device exposed by the
connection. Collections with registered model classes include only matching devices.

## Events

Events are typed dataclasses. An `UplinkEvent` exposes `data: bytes` and `f_port`
directly; an `AckEvent` exposes `queue_item_id` and `acknowledged`. Dispatch on
`event.type` or use `isinstance()` to narrow the event type without a cast.

Inventory events take only `descriptor` and `received_at`. Their network and device
identifiers are properties derived from the descriptor. The
[event guide](https://home-assistant-libs.github.io/lorawan-connection/connection/events/)
lists every field.

Version 0.11 replaces the event envelopes from 0.10; see the [migration notes](https://home-assistant-libs.github.io/lorawan-connection/connection/reference/#changes-from-010).

## Install

```sh
pip install lorawan-connection
```

The [quickstart](https://home-assistant-libs.github.io/lorawan-connection/getting-started/quickstart/)
replays a SenseCAP S2101 capture and prints 21.4 °C and 31.4% humidity.

## Backends

| Server | Install | Import |
| --- | --- | --- |
| [ChirpStack](https://home-assistant-libs.github.io/lorawan-connection/connection/chirpstack/) | `pip install "lorawan-connection[chirpstack]"` | `lorawan_connection.backend.chirpstack.ChirpStackConnection` |
| [The Things Stack](https://home-assistant-libs.github.io/lorawan-connection/connection/tts/) | `pip install "lorawan-connection[tts]"` | `lorawan_connection.backend.tts.TTSConnection` |

Adapters load only when explicitly imported. Both supply inventory before live
activity. Collections select models by catalog identity. Provision devices on the
server before connecting. The application owns connection startup, recovery, and shutdown;
missed telemetry is not replayed.

ChirpStack drops retained events older than five seconds before each device stream
starts. Keep server and client clocks synchronized. TTS supports separate Identity
and Application Servers.

## Sending commands

Models encode commands and call `async_send_downlink(data=..., f_port=...)`.
By default, the call requests a confirmed downlink and waits for its ACK. Use
`wait_for_ack=False` to return after enqueueing an unconfirmed command.

An ACK confirms delivery; device reports update model attributes. A caller can
limit its wait with `asyncio.timeout()`, but the queued command may still execute.
Optional `expires_at` limits queue retention on ChirpStack. TTS rejects explicit expiry.

The [Dragino example](https://home-assistant-libs.github.io/lorawan-connection/modelling/overview/#complete-device-example)
models the LT-22222-L with identities from both the ChirpStack and TTS catalogs.

## Testing device libraries

Use `MockConnection` from `lorawan_connection.mock` in place of a server connection.
Seed it with device descriptors, then call the collection's `async_setup()`.
`connection.emit(event)` delivers events to subscribed collections. Queued commands
are recorded in `connection.downlinks`, keyed by queue ID. Send ACK events explicitly
to test command completion; `connection.disconnect()` simulates connection loss.
See the [testing guide](https://home-assistant-libs.github.io/lorawan-connection/patterns/testing/).

## Device-library CLI

For a device library named `my_sensors` that exports `Sensors`, put this in
`my_sensors/__main__.py`:

```python
from lorawan_connection.cli_helper import run
from . import Sensors

if __name__ == "__main__":
    run(Sensors.DEVICES)
```

The helper prints public model attributes and properties as updates arrive.
Select `--backend chirpstack` (the default) or `--backend tts`. Add `--list` for
inventory only, or `--json` for machine-readable output. `--help` works without
backend extras installed.

```sh
pip install "lorawan-connection[chirpstack]"
python -m my_sensors --server https://chirpstack.example.com:443 --api-key-file /path/to/key
```

See the [CLI guide](https://home-assistant-libs.github.io/lorawan-connection/patterns/cli/)
and [Connecting to ChirpStack](https://home-assistant-libs.github.io/lorawan-connection/connection/chirpstack/).

## Add a backend

The [backend guide](https://home-assistant-libs.github.io/lorawan-connection/connection/adding-a-backend/)
covers optional dependency packaging, complete inventory, subscription ordering,
event translation, downlink acknowledgements, transport cleanup, and CLI selection.
Adapters implement the connection protocol without importing an application framework.

## Home Assistant

These APIs are proposed and are not yet part of Home Assistant:

- [Connection providers](https://home-assistant-libs.github.io/lorawan-connection/home-assistant/connection-providers/)
  configure backends and register them with `lorawan.async_register_connection()`.
- [Device implementations](https://home-assistant-libs.github.io/lorawan-connection/home-assistant/device-implementations/)
  use `DeviceManager` to access matching devices on all available connections.

## Development

```sh
uv sync --group compatibility --all-extras
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov --cov-report=term-missing
uv run python examples/replay.py
cd docs
npm ci
npm run build
```

CI tests Python 3.12, 3.13, and 3.14. Compatibility tests check event translation
against `chirpstack-api==4.19.0`. The optional backend uses the same bindings.
The documentation build validates internal links and produces `llms.txt`.

The repository follows the packaging, documentation, and release approach of
[modbus-connection](https://github.com/home-assistant-libs/modbus-connection).
The source version stays `0.0.0`; the publish workflow sets the version from the
release tag. See [releasing](RELEASING.md) for release instructions.

## Device library examples

The examples include SenseCAP S2101 and S2102, Dragino LT-22222-L and LHT65,
and Milesight TS201 and UC51x. They use official catalog identities. S2101 and
S2102 accept FPorts 1 and 2; TS201 and UC51x use 85; LHT65 uses 2.
UC51x exposes reported telemetry only.

Captured S2101, S2102, and TS201 payloads are replayed in offline regression tests.
See the [testing guide](https://home-assistant-libs.github.io/lorawan-connection/patterns/testing/)
for the fixture format and test coverage.
