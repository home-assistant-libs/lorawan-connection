---
title: Testing
description: Test device libraries using typed events and captured application bytes.
---

`MockConnection` replays inventory, delivers events, and records commands without
a server. This fixture seeds an S2101 descriptor so each test starts with a model
created through `async_setup()`:

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from lorawan_connection import DeviceDescriptor, UplinkEvent
from lorawan_connection.mock import MockConnection
from sensecap_lorawan import S2101, SenseCapDeviceCollection

DEV_EUI = "0201010101010101"


@pytest.fixture
def connection() -> MockConnection:
    descriptor = DeviceDescriptor(
        network_id="network",
        stack="chirpstack",
        dev_eui=DEV_EUI,
        name="Greenhouse",
        application_id="application",
        profile_id="profile",
        model_id=S2101.identifiers["chirpstack"][1],
        brand_id=S2101.identifiers["chirpstack"][0],
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
        UplinkEvent(
            received_at=datetime.now(UTC),
            network_id="network",
            dev_eui=DEV_EUI,
            data=bytes.fromhex("01011098530000010210A87A0000AF51"),
        )
    )
    model = devices.devices[DEV_EUI]
    assert isinstance(model, S2101)
    assert model.temperature == 21.4
    assert model.humidity == 31.4
```

Import your installed device library. This repository uses `examples/` on pytest's path.

## Captured payloads

The example libraries support SenseCAP S2101/S2102, Dragino LT-22222-L/LHT65,
and Milesight TS201/UC51x. Their model identities come from the public catalogs.
SenseCAP accepts FPorts 1 and 2. Milesight uses 85; LHT65 uses 2.
UC51x exposes reported telemetry only.

`tests/fixtures/device_uplinks/` contains raw payloads from physical S2101, S2102,
and TS201 devices, captured on 8 October 2026. The tests retain the original
application bytes and FPort, then replay them through `MockConnection` and the
vendor collection. Descriptors use synthetic identifiers and official model IDs.

For example, the S2101 fixture is:

```json
{
  "model": "S2101",
  "f_port": 2,
  "payload": "010110645f0000010210305b01001ced",
  "expected": {"temperature": 24.42, "humidity": 88.88}
}
```

Expected readings are fixed in the fixture. Do not generate them with the decoder
under test. Verify them against the vendor codec or a known device reading.
Keep credentials and deployment identifiers out of fixtures.

UC51x and LHT65 tests use published TTN codec examples. Physical-device captures
for those models are not yet available.

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
from lorawan_connection import AckEvent, DeviceDescriptor
from lorawan_connection.mock import MockConnection


@pytest.mark.asyncio
async def test_relay_command() -> None:
    descriptor = DeviceDescriptor(
        network_id="network",
        stack="chirpstack",
        dev_eui="0201010101010101",
        name="Controller",
        application_id="application",
        profile_id="profile",
        model_id=LT22222.identifiers["chirpstack"][1],
        brand_id=LT22222.identifiers["chirpstack"][0],
    )
    connection = MockConnection([descriptor])
    devices = DraginoDevices(connection)
    await devices.async_setup()
    device = devices.devices[descriptor.dev_eui]
    assert isinstance(device, LT22222)
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
                AckEvent(
                    received_at=datetime.now(UTC),
                    descriptor=descriptor,
                    queue_item_id=queue_id,
                    acknowledged=True,
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
Pass a descriptor for additions and metadata changes. For removals, supply
the last descriptor.

`connection.disconnect()` stops event delivery and calls registered disconnect
listeners once. Later subscriptions raise `ConnectionUnavailable`; command sends
raise `DownlinkError`. Close the collections when notified and create a new mock
to test reconnection.

Each mock represents one network. Add a device before emitting its activity;
unknown devices and events from another network raise `ValueError`.
