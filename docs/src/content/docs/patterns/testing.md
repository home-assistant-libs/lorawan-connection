---
title: Testing
description: Test device libraries using fixture events and real generated payloads.
---

Use a pytest fixture to create a collection and feed it an S2101 device descriptor.
Each test receives a fresh collection with the device already added.

```python
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from lorawan_connection import DeviceDescriptor, DeviceEventData, EventType, UplinkData
from sensecap_lorawan import S2101, SenseCapDeviceCollection

DEV_EUI = "0201010101010101"


@pytest.fixture
def devices() -> Iterator[SenseCapDeviceCollection]:
    devices = SenseCapDeviceCollection(network_id="network")
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

## Generated payload compatibility

`DeviceEventData` can wrap a generated message directly. The wrapper must retain
object identity. Test the same model behavior with fixtures and generated payloads
when adding a backend. Matching attribute names alone do not prove matching units
or optional-field semantics.

The optional `compatibility` dependency group pins `chirpstack-api==4.19.0` for
these checks. It does not become a runtime dependency or an install extra.
Tests cover uplinks, joins, status flags, acknowledgements, logs, and locations.
In particular, an unavailable battery reading differs from a valid zero.

```sh
pip install lorawan-connection pytest chirpstack-api==4.19.0
pytest
```

Static conformance checks for fixture dataclasses live in `tests/typing/`.
The documentation embeds the standalone example source, and CI executes it.
The real-server POC test remains outside this package's unit suite.
