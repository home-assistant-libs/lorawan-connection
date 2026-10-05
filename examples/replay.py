"""Replay an S2101 capture without a network server."""

import asyncio
from datetime import UTC, datetime

from lorawan_connection import DeviceDescriptor, DeviceEventData, EventType, UplinkData
from lorawan_connection.mock import MockConnection
from sensecap_lorawan import S2101, SenseCapDeviceCollection


async def main() -> None:
    connection = MockConnection()
    devices = SenseCapDeviceCollection(connection)

    def device_added(device: S2101) -> None:
        print(f"Device: {device.descriptor.name}")
        device.add_update_listener(
            lambda: print(
                f"temperature={device.temperature}, humidity={device.humidity}"
            )
        )

    devices.subscribe_device_added(device_added)
    descriptor = DeviceDescriptor(
        stack="chirpstack",
        network_id="home",
        dev_eui="0201010101010101",
        name="Greenhouse",
        application_id="sensors",
        profile_id="s2101-profile",
        model_id=S2101.identifiers["chirpstack"][1],
        brand_id=S2101.identifiers["chirpstack"][0],
        manufacturer="Seeed Studio",
        model="SenseCAP S2101",
    )
    now = datetime.now(UTC)
    try:
        await devices.async_setup()
        connection.emit(
            DeviceEventData(
                type=EventType.ADDED, received_at=now, descriptor=descriptor
            )
        )
        connection.emit(
            DeviceEventData(
                network_id="home",
                dev_eui=descriptor.dev_eui,
                type=EventType.UPLINK,
                received_at=now,
                data=UplinkData(bytes.fromhex("01011098530000010210A87A0000AF51")),
            )
        )
        device = devices.devices[descriptor.dev_eui]
        assert device.temperature == 21.4
        assert device.humidity == 31.4
    finally:
        devices.close()


if __name__ == "__main__":
    asyncio.run(main())
