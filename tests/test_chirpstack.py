"""Translate generated ChirpStack messages into flat immutable events."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from chirpstack_api import api, common, integration
from google.protobuf.json_format import MessageToJson

from lorawan_connection import EventType, GatewayReception
from lorawan_connection.backend.chirpstack import ChirpStackConnection

from .conftest import DESCRIPTOR


@pytest.mark.parametrize(
    "kind,message,fields",
    [
        (
            "up",
            integration.UplinkEvent(data=b"\x00\xff", f_port=42),
            {"data": b"\x00\xff", "f_port": 42, "receptions": ()},
        ),
        (
            "up",
            integration.UplinkEvent(
                data=b"payload",
                rx_info=[
                    {"gateway_id": "nearby", "rssi": -45, "snr": -1.5},
                    {"gateway_id": "rooftop", "rssi": -85, "snr": 4.0},
                ],
            ),
            {
                "receptions": (
                    GatewayReception("nearby", -45, -1.5),
                    GatewayReception("rooftop", -85, 4.0),
                )
            },
        ),
        ("join", integration.JoinEvent(dev_addr="abcdef01"), {"dev_addr": "abcdef01"}),
        (
            "status",
            integration.StatusEvent(margin=-2, battery_level=0),
            {
                "margin": -2,
                "battery_level": 0,
                "battery_level_unavailable": False,
                "external_power_source": False,
            },
        ),
        (
            "ack",
            integration.AckEvent(queue_item_id="queue", acknowledged=True),
            {"queue_item_id": "queue", "acknowledged": True},
        ),
        (
            "txack",
            integration.TxAckEvent(gateway_id="gateway", downlink_id=123),
            {"gateway_id": "gateway", "downlink_id": 123},
        ),
        (
            "log",
            integration.LogEvent(description="diagnostic", level=1, code=2),
            {"description": "diagnostic", "level": 1, "code": 2},
        ),
        (
            "location",
            integration.LocationEvent(
                location=common.Location(latitude=52.5, longitude=4.5, altitude=2)
            ),
            {"latitude": 52.5, "longitude": 4.5, "altitude": 2},
        ),
    ],
)
async def test_event_conversion(kind, message, fields) -> None:
    message.device_info.dev_eui = DESCRIPTOR.dev_eui

    async def stream(*args, **kwargs) -> AsyncIterator[api.LogItem]:
        yield api.LogItem(
            id=f"{int(datetime.now(UTC).timestamp() * 1000)}-1",
            description=kind,
            body=MessageToJson(message),
        )

    connection = ChirpStackConnection(
        "http://localhost:1",
        "key",
        application_ids=["app"],
        network_id="network",
        channel=Mock(close=AsyncMock()),
    )
    connection._internal_api.StreamDeviceEvents = stream
    events = [event async for event in connection._read_stream(DESCRIPTOR.dev_eui)]
    assert len(events) == 1
    event = events[0]
    assert event.type is EventType(kind)
    assert event.dev_eui == DESCRIPTOR.dev_eui
    for name, value in fields.items():
        assert getattr(event, name) == value
    message.Clear()
    for name, value in fields.items():
        assert getattr(event, name) == value
    await connection.close()
