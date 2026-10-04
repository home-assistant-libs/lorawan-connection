---
title: Testing
description: Test device libraries using fixture events and real generated payloads.
---

`MockConnection` implements the same connection protocol as a network backend.
It replays existing devices when a collection subscribes, delivers events to the
subscribed vendors, and records outgoing commands. It needs no server or radio.

Use a fixture to seed the connection with an S2101 descriptor. Each test receives
a fresh collection with that device already added through `async_setup()`.

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from lorawan_connection import DeviceDescriptor, DeviceEventData, EventType, UplinkData
from lorawan_connection.mock import MockConnection
from sensecap_lorawan import S2101, SenseCapDeviceCollection

DEV_EUI = "0201010101010101"


@pytest.fixture
def connection() -> MockConnection:
    descriptor = DeviceDescriptor(
        network_id="network",
        dev_eui=DEV_EUI,
        name="Greenhouse",
        application_id="application",
        profile_id="profile",
        catalog_model_id=S2101.catalog_model_id,
        vendor_id=S2101.vendor_id,
    )
    return MockConnection([descriptor])


@pytest_asyncio.fixture
async def devices(
    connection: MockConnection,
) -> AsyncIterator[SenseCapDeviceCollection]:
    devices = SenseCapDeviceCollection(connection)
    await devices.async_setup()
    try:
        yield devices
    finally:
        devices.close()


@pytest.mark.asyncio
async def test_s2101(
    devices: SenseCapDeviceCollection, connection: MockConnection
) -> None:
    connection.emit(
        DeviceEventData(
            type=EventType.UPLINK,
            received_at=datetime.now(UTC),
            network_id="network",
            dev_eui=DEV_EUI,
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

Test existing devices before any telemetry, repeated device descriptions, metadata changes, catalog
identity changes, and removal. Subscribe after models exist to check initial replay.
Test a second network with the same DevEUI to verify isolation.

For decoders, cover zero, negative values, unknown channels, unsupported ports,
partial measurements, malformed lengths, and vendor error sentinels. Assert that
invalid frames leave existing state unchanged.

For listeners, test unsubscribe and close. Repeated cleanup should be harmless.
A listener exception must not prevent other consumers from receiving updates.

## Test commands

`connection.downlinks` records each `Downlink` under the queue ID returned to the
model. Assert its bytes, DevEUI, port, confirmation flag, and expiry. The mock does
not acknowledge commands automatically. Emit an `ACK` with that queue ID to
complete the command.

For example, this test exercises the Dragino library's relay command:

```python
import asyncio
from datetime import UTC, datetime

import pytest

from dragino_lorawan import DraginoDevices, LT22222
from lorawan_connection import AckData, DeviceDescriptor, DeviceEventData, EventType
from lorawan_connection.mock import MockConnection


@pytest.mark.asyncio
async def test_relay_command() -> None:
    descriptor = DeviceDescriptor(
        network_id="network",
        dev_eui="0201010101010101",
        name="Controller",
        application_id="application",
        profile_id="profile",
        catalog_model_id=LT22222.catalog_model_id,
        vendor_id=LT22222.vendor_id,
    )
    connection = MockConnection([descriptor])
    devices = DraginoDevices(connection)
    await devices.async_setup()
    device = devices.devices[descriptor.dev_eui]
    try:
        async with asyncio.TaskGroup() as tasks:
            command = tasks.create_task(device.async_set_relay(1, True))
            await asyncio.sleep(0)
            queue_id, downlink = next(iter(connection.downlinks.items()))
            assert downlink.data == bytes.fromhex("030111")
            assert downlink.f_port == 2
            assert downlink.confirmed
            assert not command.done()
            connection.emit(
                DeviceEventData(
                    type=EventType.ACK,
                    received_at=datetime.now(UTC),
                    descriptor=descriptor,
                    data=AckData(queue_id, True),
                )
            )
            await command
        assert device.relays[1] is None
    finally:
        devices.close()
```

Cover negative ACKs and cancellation too. Verify that ACKs leave reported state
unchanged and that a later uplink updates it. To simulate a send failure, patch
`connection.async_send_downlink` to raise `DownlinkError`.

## Simulate removal and disconnects

Emit `ADDED`, `UPDATED`, and `REMOVED` events to change which devices are available.
Pass a descriptor for additions and metadata changes. For removals, either supply
the descriptor or the network and device identifiers.

`connection.disconnect()` stops event delivery and calls registered disconnect
listeners once. Later subscriptions raise `ConnectionUnavailable`; command sends
raise `DownlinkError`. The application must close its collections when notified,
just as it does with a real connection. Create a new mock connection to test
reconnection.

Each mock represents one network. Add a device before emitting its activity;
unknown devices and events from another network raise `ValueError`.
