---
title: Testing
description: Test device libraries using fixture events and real generated payloads.
---

Use a pytest fixture to create a collection and feed it an S2101 device descriptor.
Each test receives a fresh collection with the device already added.

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from lorawan_connection import DeviceDescriptor, DeviceEventData, EventType, UplinkData
from sensecap_lorawan import S2101, SenseCapDeviceCollection

DEV_EUI = "0201010101010101"


@pytest.fixture
def devices() -> Iterator[SenseCapDeviceCollection]:
    connection = Mock(
        network_id="network", async_send_downlink=AsyncMock(return_value="queue-id")
    )
    devices = SenseCapDeviceCollection(connection)
    descriptor = DeviceDescriptor(
        "network",
        DEV_EUI,
        "Greenhouse",
        "application",
        "profile",
        catalog_model_id=S2101.catalog_model_id,
        vendor_id=S2101.vendor_id,
    )
    devices.handle_event(
        DeviceEventData(
            "network",
            descriptor.dev_eui,
            EventType.ADDED,
            datetime.now(UTC),
            descriptor,
        )
    )
    yield devices
    devices.close()


def test_s2101(devices: SenseCapDeviceCollection) -> None:
    devices.handle_event(
        DeviceEventData(
            "network",
            DEV_EUI,
            EventType.UPLINK,
            datetime.now(UTC),
            data=UplinkData(bytes.fromhex("01011098530000010210A87A0000AF51")),
        )
    )
    model = devices.devices[DEV_EUI]
    assert model.temperature == 21.4
    assert model.humidity == 31.4
```

This repository adds `examples/` to pytest's path so that the example library is
importable. In your library repository, import your installed development package.

## Event sequences to cover

Test inventory before any telemetry, repeated inventory, metadata changes, catalog
identity changes, and removal. Subscribe after models exist to check initial replay.
Test a second network with the same DevEUI to verify isolation.

For decoders, cover zero, negative values, unknown channels, unsupported ports,
partial measurements, malformed lengths, and vendor error sentinels. Assert that
invalid frames leave existing state unchanged.

For listeners, test unsubscribe and close. Repeated cleanup should be harmless.
A listener exception must not prevent other consumers from receiving updates.

## Test commands

Use an `AsyncMock` for the connection's `async_send_downlink` method and assert the
encoded bytes, DevEUI, port, and expiry.
Feed an ACK with the sender's queue-item ID to complete the command. Cover positive
and negative ACKs, sender failures, and cancellation. Verify that ACKs leave
reported state unchanged and that a later uplink updates it.

## Test with ChirpStack payloads

To test with generated ChirpStack messages, install the `chirpstack` extra.
Pass a generated message as `DeviceEventData.data`, just as you pass `UplinkData`
in the example above.

```sh
pip install "lorawan-connection[chirpstack]" pytest
pytest
```
