"""Exercise collection contracts and the real generated payload interface."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from lorawan_connection import (
    DeviceDescriptor,
    DeviceEventData,
    EventType,
    UplinkData,
)
from sensecap_lorawan import (
    S2101,
    SenseCapDeviceCollection,
    decode_s2101,
)

NOW = datetime.now(UTC)
DESCRIPTOR = DeviceDescriptor(
    "network",
    "0201010101010101",
    "Greenhouse",
    "application",
    "profile",
    S2101.catalog_model_id,
    S2101.vendor_id,
)
PAYLOAD = bytes.fromhex("01011098530000010210A87A0000AF51")


def inventory(
    descriptor: DeviceDescriptor = DESCRIPTOR, kind: EventType = EventType.ADDED
) -> DeviceEventData:
    """Build an inventory fixture."""
    return DeviceEventData(
        descriptor.network_id, descriptor.dev_eui, kind, NOW, descriptor
    )


def test_collection_lifecycle() -> None:
    """Initial replay, idempotence, independent devices and retirement."""
    collection = SenseCapDeviceCollection(Mock(network_id="network"))
    collection.handle_event(inventory())
    added, removed = Mock(), Mock()
    stop = collection.subscribe_device_added(added)
    collection.subscribe_device_removed(removed)
    added.assert_called_once()
    model = added.call_args.args[0]
    collection.handle_event(inventory())
    collection.handle_event(
        inventory(replace(DESCRIPTOR, name="Renamed"), EventType.UPDATED)
    )
    added.assert_called_once()
    assert model.descriptor.name == "Renamed"
    collection.handle_event(inventory(replace(DESCRIPTOR, dev_eui="0201010101010102")))
    assert added.call_count == 2
    collection.handle_event(
        inventory(
            replace(DESCRIPTOR, catalog_model_id="unsupported"), EventType.UPDATED
        )
    )
    assert model.closed
    removed.assert_called_once_with(model)
    stop()
    stop()
    collection.handle_event(inventory())
    assert added.call_count == 2
    collection.close()
    collection.close()
    assert not collection.devices
    collection.handle_event(inventory())
    assert not collection.devices


@pytest.mark.parametrize("generated", [False, True])
def test_zero_copy_payload_and_partial_state(generated: bool) -> None:
    """The same model consumes fixture and generated payloads by reference."""
    payload_type = UplinkData
    if generated:
        integration = pytest.importorskip("chirpstack_api.integration")
        payload_type = integration.UplinkEvent
    collection = SenseCapDeviceCollection(Mock(network_id="network"))
    collection.handle_event(inventory())
    device = collection.devices[DESCRIPTOR.dev_eui]
    listener = Mock()
    stop = device.add_update_listener(listener)
    payload = payload_type(data=PAYLOAD, f_port=1)
    event = DeviceEventData(
        "network", DESCRIPTOR.dev_eui, EventType.UPLINK, NOW, data=payload
    )
    assert event.data is payload
    collection.handle_event(event)
    assert device.temperature == 21.4
    assert device.humidity == 31.4
    listener.assert_called_once()
    collection.handle_event(event)
    listener.assert_called_once()
    partial = payload_type(data=bytes.fromhex("010110F0D8FFFF0000"), f_port=1)
    collection.handle_event(
        replace(event, received_at=NOW + timedelta(seconds=1), data=partial)
    )
    assert device.temperature == -10
    assert device.humidity == 31.4
    collection.handle_event(event)
    assert device.temperature == -10
    stop()
    collection.close()


@pytest.mark.parametrize(
    "payload", [b"", b"\x01", PAYLOAD[:-1], bytes.fromhex("010210FFFFFFFF0000")]
)
def test_malformed_payload_atomic(payload: bytes) -> None:
    """Invalid frames cannot partially modify existing state."""
    with pytest.raises(ValueError):
        decode_s2101(payload)


def test_zero_unknown_fields_and_port() -> None:
    """Zero is meaningful; unknown record types and FPorts do not erase it."""
    assert decode_s2101(bytes.fromhex("01011000000000010210000000000000")) == {
        "temperature": 0,
        "humidity": 0,
    }
    assert decode_s2101(bytes.fromhex("010700640000000000")) == {}
    collection = SenseCapDeviceCollection(Mock(network_id="network"))
    collection.handle_event(inventory())
    collection.handle_event(
        DeviceEventData(
            "network",
            DESCRIPTOR.dev_eui,
            EventType.UPLINK,
            NOW,
            data=UplinkData(PAYLOAD, 2),
        )
    )
    assert collection.devices[DESCRIPTOR.dev_eui].temperature is None


def test_unknown_device_network_and_vendor() -> None:
    """Payload, name, or wrong vendor cannot create an unrecognized model."""
    collection = SenseCapDeviceCollection(Mock(network_id="network"))
    collection.handle_event(
        DeviceEventData(
            "network",
            DESCRIPTOR.dev_eui,
            EventType.UPLINK,
            NOW,
            data=UplinkData(PAYLOAD),
        )
    )
    collection.handle_event(inventory(replace(DESCRIPTOR, network_id="other")))
    collection.handle_event(inventory(replace(DESCRIPTOR, vendor_id=1)))
    assert not collection.devices


def test_listener_exception_and_unsubscribe() -> None:
    """One consumer cannot stop another receiving devices."""
    collection = SenseCapDeviceCollection(Mock(network_id="network"))
    collection.subscribe_device_added(Mock(side_effect=ValueError))
    listener = Mock()
    collection.subscribe_device_added(listener)
    collection.handle_event(inventory())
    listener.assert_called_once()


@pytest.mark.parametrize("eui", ["xyz", "0" * 17, "g" * 16])
def test_invalid_eui(eui: str) -> None:
    """Reject invalid identities at the descriptor boundary."""
    with pytest.raises(ValueError):
        replace(DESCRIPTOR, dev_eui=eui)


def test_same_millisecond_partial_updates() -> None:
    """Different live readings may share a server receipt timestamp."""
    collection = SenseCapDeviceCollection(Mock(network_id="network"))
    collection.handle_event(inventory())
    event = DeviceEventData(
        "network", DESCRIPTOR.dev_eui, EventType.UPLINK, NOW, data=UplinkData(PAYLOAD)
    )
    collection.handle_event(event)
    collection.handle_event(
        replace(event, data=UplinkData(bytes.fromhex("010110000000000000")))
    )
    device = collection.devices[DESCRIPTOR.dev_eui]
    assert device.temperature == 0
    assert device.humidity == 31.4
    collection.close()


def test_invalid_and_older_frames_preserve_model_state() -> None:
    collection = SenseCapDeviceCollection(Mock(network_id="network"))
    collection.handle_event(inventory())
    event = DeviceEventData(
        "network", DESCRIPTOR.dev_eui, EventType.UPLINK, NOW, data=UplinkData(PAYLOAD)
    )
    collection.handle_event(event)
    device = collection.devices[DESCRIPTOR.dev_eui]
    original = (device.temperature, device.humidity)
    collection.handle_event(replace(event, data=UplinkData(b"invalid")))
    collection.handle_event(replace(event, received_at=NOW - timedelta(seconds=1)))
    assert (device.temperature, device.humidity) == original


def test_temperature_range_and_unknown_channel() -> None:
    with pytest.raises(ValueError, match="Temperature outside"):
        decode_s2101(
            bytes.fromhex("010110")
            + (86000).to_bytes(4, "little", signed=True)
            + b"\x00\x00"
        )
    assert decode_s2101(bytes.fromhex("020110000000000000")) == {}
