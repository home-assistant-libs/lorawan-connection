---
title: Connecting to The Things Stack
description: Read devices and live traffic through The Things Stack gRPC APIs.
---

The Things Stack backend uses gRPC for inventory, application traffic, lifecycle
events, and downlinks. It works with devices already registered in the selected
applications. It does not provision devices or require MQTT.

Install its optional dependencies:

```sh
pip install "lorawan-connection[tts]"
```

## Connect a device library

Use the [SenseCAP example library](/lorawan-connection/getting-started/quickstart/#example-library)
as `sensecap_lorawan.py`. Set `TTS_API_KEY` and `TTS_APPLICATION_ID`, then run:

```python
import asyncio
import os

from lorawan_connection.backend.tts import TTSConnection
from sensecap_lorawan import SenseCapDeviceCollection


async def main() -> None:
    connection = TTSConnection(
        "https://application-server.example.com:8884",
        os.environ["TTS_API_KEY"],
        identity_server="https://identity-server.example.com:8884",
        application_ids=[os.environ["TTS_APPLICATION_ID"]],
        network_id="my-tts-network",
    )
    devices = SenseCapDeviceCollection(connection)
    disconnected = asyncio.Event()
    unsubscribe = connection.on_disconnect(disconnected.set)

    def added(device):
        device.add_update_listener(lambda: print(device.temperature, device.humidity))

    unsubscribe_added = devices.subscribe_device_added(added)
    try:
        await connection.async_connect()
        await devices.async_setup()
        await disconnected.wait()
        raise connection.error or RuntimeError("Disconnected")
    finally:
        unsubscribe_added()
        unsubscribe()
        devices.close()
        await connection.close()


asyncio.run(main())
```

Use the gRPC endpoints supplied by your deployment. Omit `identity_server` when
both services use the same endpoint. `https` verifies certificates with system
trust roots. `http` explicitly enables plaintext for a trusted local server.

Select application IDs explicitly. Application-scoped API keys do not need user
or administrator access. The key needs `RIGHT_APPLICATION_DEVICES_READ` and
`RIGHT_APPLICATION_TRAFFIC_READ`. Add `RIGHT_APPLICATION_TRAFFIC_DOWN_WRITE` for
commands. A key must cover every selected application.

Device `version_ids.brand_id` and `version_ids.model_id` select models in the
`tts` catalog namespace. Devices without a DevEUI are skipped. Devices without
catalog identity remain in inventory but do not select a vendor model.

## Lifecycle and recovery

The adapter reads complete inventory snapshots and starts application and device
lifecycle streams. It reconciles inventory after the lifecycle subscription starts.
It also refreshes every 30 seconds to catch missed lifecycle events. An incomplete
read does not replace the previous inventory.

An unknown-device uplink triggers inventory refresh before delivery. Raw uplinks,
join events, locations, positive and negative acknowledgements, and command failures
map to shared library events. Encrypted application payloads are rejected because
device libraries need plaintext bytes. Unsupported application messages are ignored.
The adapter does not synthesize status, gateway transmission, or general log events.

A failed stream withdraws availability and notifies disconnect listeners once.
The application owns reconnection: close the failed adapter and create a replacement.
`AuthenticationError` reports rejected credentials or missing read permissions.
`ConnectionUnavailable` reports connection failures. Closing the adapter cancels
its tasks and closes both gRPC channels.

The TTS application stream has no readiness acknowledgement before its first
message. Setup checks application read rights and the lifecycle stream. Device
readings can remain unknown until the first uplink; missed telemetry is not replayed.

## Commands and expiry

Commands use `AppAs.DownlinkQueuePush`. A generated correlation ID connects an
ACK, NACK, or downlink failure to the waiting model. A read-only write fails with
`DownlinkError` and leaves the connection available. Commands are never retried.

TTS has no queue-expiry field. The adapter rejects a downlink with `expires_at`
before enqueueing it. The Dragino relay and digital-output methods default to no
expiry and can run through TTS. A local timeout stops waiting but does not remove
a queued command; it may be delivered later.

Queue clearing is a separate operation that needs a live server connection. It
cannot undo a command already sent and can remove other pending commands. The
backend does not clear queues automatically when a command times out.

## CLI

The shared helper selects the backend and imports it only when connecting:

```sh
python -m my_sensors --backend tts \
  --server https://application-server.example.com:8884 \
  --identity-server https://identity-server.example.com:8884 \
  --application my-application --api-key-file /path/to/key --json
```

The wheel contains a private descriptor set generated from the official TTS 3.36.2
API definitions. No protobuf compiler or source checkout is needed at runtime.
The adapter uses a private descriptor pool and does not install a `ttn` namespace.

References: [application API](https://www.thethingsindustries.com/docs/api/reference/grpc/application_server/)
and [events API](https://www.thethingsindustries.com/docs/api/reference/grpc/events/).
