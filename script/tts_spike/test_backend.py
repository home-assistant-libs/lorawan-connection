"""Adapter behavior independent of a running TTS server."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import grpc
import pytest
from backend import TTSConnection
from ttn.lorawan.v3 import messages_pb2 as messages

from lorawan_connection import DeviceDescriptor, Downlink, DownlinkError, EventType

DESCRIPTOR = DeviceDescriptor(
    stack="tts",
    network_id="test",
    dev_eui="0000000000000001",
    name="Sensor",
    application_id="app",
    profile_id="",
    brand_id="sensecap",
    model_id="sensor",
)


@pytest.fixture
async def connection():
    connection = TTSConnection(
        grpc.aio.insecure_channel("127.0.0.1:1"),
        "unused",
        application_ids=["app"],
        network_id="test",
    )
    connection.available = True
    connection.devices[DESCRIPTOR.dev_eui] = DESCRIPTOR
    yield connection
    await connection.close()


async def test_unknown_device_refresh_precedes_uplink(connection):
    connection.devices.clear()
    received = []
    await connection.async_subscribe(
        brands=frozenset({("tts", "sensecap")}), callback=received.append
    )

    def refresh():
        connection.devices[DESCRIPTOR.dev_eui] = DESCRIPTOR
        connection._emit(connection._event(EventType.ADDED, DESCRIPTOR))

    connection.refresh = AsyncMock(side_effect=refresh)
    message = messages.ApplicationUp(
        end_device_ids={"dev_eui": bytes.fromhex(DESCRIPTOR.dev_eui)},
        uplink_message={"f_port": 1, "frm_payload": b"data"},
    )
    await connection.handle_message(message)
    connection.refresh.assert_awaited_once()
    assert [event.type for event in received] == [EventType.ADDED, EventType.UPLINK]
    assert received[-1].data.data == b"data"


@pytest.mark.parametrize(
    "field,acknowledged", [("downlink_ack", True), ("downlink_nack", False)]
)
async def test_ack_uses_our_correlation_id(connection, field, acknowledged):
    received = []
    await connection.async_subscribe(
        brands=frozenset({("tts", "sensecap")}), callback=received.append
    )
    message = messages.ApplicationUp(
        end_device_ids={"dev_eui": bytes.fromhex(DESCRIPTOR.dev_eui)},
        **{field: {"correlation_ids": ["other", "lorawan-connection:test"]}},
    )
    await connection.handle_message(message)
    assert received[-1].data.queue_item_id == "lorawan-connection:test"
    assert received[-1].data.acknowledged is acknowledged


async def test_expiry_is_not_silently_dropped(connection):
    with pytest.raises(DownlinkError, match="expiry"):
        await connection.async_send_downlink(
            Downlink(
                dev_eui=DESCRIPTOR.dev_eui,
                f_port=1,
                data=b"data",
                expires_at=datetime.now(UTC),
            )
        )


async def test_disconnect_notifies_once_and_disables_subscription(connection):
    callback = Mock()
    connection.on_disconnect(callback)
    connection._failed(RuntimeError("stream closed"))
    connection._failed(RuntimeError("another stream closed"))
    assert not connection.available
    callback.assert_called_once_with()
