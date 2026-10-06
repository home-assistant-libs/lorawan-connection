"""Identity validation and fixture payload ownership."""

from dataclasses import FrozenInstanceError, replace

import pytest

from lorawan_connection import (
    AckEvent,
    AddedEvent,
    EventType,
    JoinEvent,
    LocationEvent,
    LogEvent,
    RemovedEvent,
    StatusEvent,
    TxAckEvent,
    UpdatedEvent,
    UplinkEvent,
)

from .conftest import DESCRIPTOR, NOW


@pytest.mark.parametrize(
    "eui", ["", "xyz", "0" * 17, "g" * 16, "00-11-22-33-44-55-66-77"]
)
def test_invalid_dev_eui(eui: str) -> None:
    with pytest.raises(ValueError, match="eight hexadecimal bytes"):
        replace(DESCRIPTOR, dev_eui=eui)


def test_normalized_frozen_descriptor() -> None:
    descriptor = replace(DESCRIPTOR, dev_eui="AA:BB:CC:DD:EE:FF:00:11")
    assert descriptor.dev_eui == "aabbccddeeff0011"
    with pytest.raises(FrozenInstanceError):
        descriptor.name = "changed"  # type: ignore[misc]


def test_payload_bytes_are_not_copied() -> None:
    payload = b"\x01"
    event = UplinkEvent(descriptor=DESCRIPTOR, received_at=NOW, data=payload, f_port=10)
    assert event.data is payload
    assert event.f_port == 10
    assert event.type is EventType.UPLINK
    with pytest.raises(FrozenInstanceError):
        event.data = b"changed"  # type: ignore[misc]
    with pytest.raises((TypeError, ValueError), match="init=False"):
        replace(event, type=EventType.ACK)


def test_event_derives_identity_without_copying_descriptor() -> None:
    event = AddedEvent(received_at=NOW, descriptor=DESCRIPTOR)
    assert event.network_id == DESCRIPTOR.network_id
    assert event.dev_eui == DESCRIPTOR.dev_eui
    assert event.descriptor is DESCRIPTOR
    assert replace(event, received_at=NOW).descriptor is DESCRIPTOR
    with pytest.raises((FrozenInstanceError, TypeError)):
        event.dev_eui = "0000000000000002"  # type: ignore[misc]


def test_event_accepts_matching_explicit_identity() -> None:
    descriptor = replace(DESCRIPTOR, dev_eui="aabbccddeeff0011")
    event = UplinkEvent(
        data=b"",
        received_at=NOW,
        descriptor=descriptor,
        network_id=descriptor.network_id,
        dev_eui="AA:BB:CC:DD:EE:FF:00:11",
    )
    assert event.dev_eui == descriptor.dev_eui


@pytest.mark.parametrize(
    "identity", [{"network_id": "other"}, {"dev_eui": "0000000000000002"}]
)
def test_event_rejects_conflicting_identity(identity: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="conflicts with descriptor"):
        UplinkEvent(received_at=NOW, descriptor=DESCRIPTOR, data=b"", **identity)


@pytest.mark.parametrize(
    "identity", [{}, {"network_id": "network"}, {"dev_eui": DESCRIPTOR.dev_eui}]
)
def test_event_requires_identity_without_descriptor(identity: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="required without a descriptor"):
        UplinkEvent(received_at=NOW, **identity, data=b"")


def test_event_defaults_and_flat_fields() -> None:
    identity = {"descriptor": DESCRIPTOR, "received_at": NOW}
    assert UplinkEvent(**identity, data=b"").f_port == 1
    assert StatusEvent(**identity).battery_level_unavailable
    assert (
        StatusEvent(
            **identity, battery_level=0, battery_level_unavailable=False
        ).battery_level
        == 0
    )
    assert (
        LocationEvent(**identity, latitude=52, longitude=4, altitude=3).latitude == 52
    )
    assert JoinEvent(**identity, dev_addr="12345678").dev_addr == "12345678"
    assert (
        AckEvent(**identity, queue_item_id="queue", acknowledged=False).acknowledged
        is False
    )
    assert TxAckEvent(**identity, gateway_id="gateway", downlink_id=1).downlink_id == 1
    assert LogEvent(**identity, description="message", level=1, code=2).code == 2


@pytest.mark.parametrize("event_class", [AddedEvent, UpdatedEvent, RemovedEvent])
def test_inventory_identity_is_derived(event_class) -> None:
    """Inventory constructors accept no duplicate identity or discriminator."""
    import inspect

    assert set(inspect.signature(event_class).parameters) == {
        "descriptor",
        "received_at",
    }
    event = event_class(descriptor=DESCRIPTOR, received_at=NOW)
    changed = replace(
        event,
        descriptor=replace(DESCRIPTOR, network_id="other", dev_eui="0000000000000002"),
    )
    assert changed.network_id == "other"
    assert changed.dev_eui == "0000000000000002"


def test_invalid_activity_identity() -> None:
    with pytest.raises(ValueError, match="eight hexadecimal bytes"):
        UplinkEvent(network_id="network", dev_eui="invalid", received_at=NOW, data=b"")
