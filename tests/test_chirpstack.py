"""Generated payload compatibility, without a server or runtime dependency."""

from typing import cast

import pytest

from lorawan_connection import (
    Ack,
    Coordinates,
    DeviceEventData,
    EventType,
    Join,
    Location,
    Log,
    Status,
    TxAck,
    Uplink,
)

from .conftest import DESCRIPTOR, NOW

integration = pytest.importorskip("chirpstack_api.integration")
common = pytest.importorskip("chirpstack_api.common")


def test_uplink_protocol() -> None:
    message = integration.UplinkEvent(data=b"\x00\xff", f_port=42)
    payload = cast(Uplink, message)
    assert payload.data == b"\x00\xff"
    assert payload.f_port == 42
    event = DeviceEventData(
        network_id="network",
        dev_eui=DESCRIPTOR.dev_eui,
        type=EventType.UPLINK,
        received_at=NOW,
        data=payload,
    )
    assert event.data is message


def test_status_presence_and_values() -> None:
    unavailable = cast(Status, integration.StatusEvent(battery_level_unavailable=True))
    assert unavailable.battery_level_unavailable
    payload = cast(
        Status,
        integration.StatusEvent(
            margin=-2, battery_level=0, external_power_source=False
        ),
    )
    assert payload.margin == -2
    assert payload.battery_level == 0
    assert not payload.battery_level_unavailable
    assert not payload.external_power_source


def test_other_generated_payload_shapes() -> None:
    join = cast(Join, integration.JoinEvent(dev_addr="abcdef01"))
    assert join.dev_addr == "abcdef01"
    ack = cast(Ack, integration.AckEvent(queue_item_id="queue", acknowledged=True))
    assert ack.queue_item_id == "queue"
    assert ack.acknowledged is True
    tx_ack = cast(TxAck, integration.TxAckEvent(gateway_id="gateway", downlink_id=123))
    assert tx_ack.gateway_id == "gateway"
    assert tx_ack.downlink_id == 123
    log = cast(Log, integration.LogEvent(description="diagnostic", level=1, code=2))
    assert log.description == "diagnostic"
    assert log.level == 1
    assert log.code == 2
    location = cast(
        Location,
        integration.LocationEvent(
            location=common.Location(latitude=52.5, longitude=4.5, altitude=2)
        ),
    )
    coordinates: Coordinates = location.location
    assert coordinates.latitude == 52.5
    assert coordinates.longitude == 4.5
    assert coordinates.altitude == 2
