"""Replay an S2101 capture without a network server."""

from datetime import UTC, datetime

from lorawan_connection import DeviceDescriptor, DeviceEventData, EventType, UplinkData
from sensecap_lorawan import S2101, S2101_MODEL_ID, VENDOR_ID, SenseCapDeviceCollection


def main() -> None:
    devices = SenseCapDeviceCollection("home")

    def device_added(device: S2101) -> None:
        print(f"Device: {device.descriptor.name}")
        device.subscribe(lambda state: print(f"State: {state}"))

    devices.subscribe_device_added(device_added)
    descriptor = DeviceDescriptor(
        network_id="home",
        dev_eui="0201010101010101",
        name="Greenhouse",
        application_id="sensors",
        profile_id="s2101-profile",
        catalog_model_id=S2101_MODEL_ID,
        vendor_id=VENDOR_ID,
        manufacturer="Seeed Studio",
        model="SenseCAP S2101",
    )
    now = datetime.now(UTC)
    try:
        devices.handle_event(
            DeviceEventData(
                "home", descriptor.dev_eui, EventType.ADDED, now, descriptor
            )
        )
        devices.handle_event(
            DeviceEventData(
                "home",
                descriptor.dev_eui,
                EventType.UPLINK,
                now,
                data=UplinkData(bytes.fromhex("01011098530000010210A87A0000AF51")),
            )
        )
        device = devices.devices[descriptor.dev_eui]
        assert device.state.temperature == 21.4
        assert device.state.humidity == 31.4
    finally:
        devices.close()


if __name__ == "__main__":
    main()
