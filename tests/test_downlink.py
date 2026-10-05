"""Commands preserve device identity, scope, and asynchronous error behavior."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import grpc
import pytest
from chirpstack_api import api

from dragino_lorawan import LT22222, DraginoDevices
from lorawan_connection import (
    AckData,
    DeviceEventData,
    Downlink,
    DownlinkError,
    EventType,
)
from lorawan_connection.backend.chirpstack import ChirpStackConnection
from lorawan_connection.mock import MockConnection

from .conftest import DESCRIPTOR, inventory
from .test_chirpstack_connection import connection as connection


@pytest.mark.parametrize(
    "method,channel,on,payload",
    [
        ("async_set_relay", 1, True, "030111"),
        ("async_set_relay", 1, False, "030011"),
        ("async_set_relay", 2, True, "031101"),
        ("async_set_relay", 2, False, "031100"),
        ("async_set_digital_output", 1, True, "02011111"),
        ("async_set_digital_output", 1, False, "02001111"),
        ("async_set_digital_output", 2, True, "02110111"),
        ("async_set_digital_output", 2, False, "02110011"),
    ],
)
async def test_output_commands(
    method: str, channel: int, on: bool, payload: str
) -> None:
    descriptor = replace(
        DESCRIPTOR,
        brand_id=LT22222.identifiers["chirpstack"][0],
        model_id=LT22222.identifiers["chirpstack"][1],
    )
    mock_connection = MockConnection([descriptor])
    collection = DraginoDevices(mock_connection)
    await collection.async_setup()
    device = collection.devices[descriptor.dev_eui]

    before = datetime.now(UTC)
    command = getattr(device, method)
    pending = asyncio.create_task(command(channel, on))
    await asyncio.sleep(0)
    queue_id, request = next(iter(mock_connection.downlinks.items()))
    mock_connection.emit(
        DeviceEventData(
            type=EventType.ACK,
            received_at=datetime.now(UTC),
            descriptor=descriptor,
            data=AckData(queue_id, True),
        )
    )
    assert await pending is None
    assert request.dev_eui == descriptor.dev_eui
    assert request.data == bytes.fromhex(payload)
    assert request.f_port == 2
    assert request.confirmed
    assert (
        before + timedelta(seconds=30)
        <= request.expires_at
        <= datetime.now(UTC) + timedelta(seconds=30)
    )
    assert device.relays == {1: None, 2: None}
    assert device.digital_outputs == {1: None, 2: None}
    mock_connection.emit(inventory(descriptor, EventType.REMOVED))
    with pytest.raises(DownlinkError, match="closed"):
        await command(1, True)
    assert len(mock_connection.downlinks) == 1
    collection.close()


@pytest.mark.parametrize("method", ["async_set_relay", "async_set_digital_output"])
async def test_missing_sender_and_invalid_channel(method: str) -> None:
    device = LT22222(DESCRIPTOR)
    command = getattr(device, method)
    with pytest.raises(DownlinkError, match="sender"):
        await command(1, True)
    with pytest.raises(ValueError, match="channel"):
        await command(3, True)


@pytest.mark.parametrize("port", [0, 224, 256])
def test_reserved_port(port: int) -> None:
    with pytest.raises(ValueError, match="FPort"):
        Downlink(DESCRIPTOR.dev_eui, port, b"command")


def test_expiry_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Downlink(DESCRIPTOR.dev_eui, 2, b"command", expires_at=datetime(2026, 1, 1))


def prepare(connection: ChirpStackConnection) -> None:
    connection.available = True
    connection.devices = {DESCRIPTOR.dev_eui: DESCRIPTOR}
    connection._device_api.Get = AsyncMock(
        return_value=api.GetDeviceResponse(
            device=api.Device(application_id="application")
        )
    )
    connection._device_api.Enqueue = AsyncMock(
        return_value=api.EnqueueDeviceQueueItemResponse(id="queue-id")
    )


async def test_enqueue_request(connection: ChirpStackConnection) -> None:
    prepare(connection)
    expiry = datetime.now(UTC) + timedelta(seconds=30)
    downlink = Downlink(DESCRIPTOR.dev_eui, 2, b"\x03\x01\x11", True, expiry)
    assert await connection.async_send_downlink(downlink) == "queue-id"
    call = connection._device_api.Enqueue.call_args
    assert call.kwargs == {"metadata": connection.metadata, "timeout": 15}
    assert call.args[0].queue_item.data == downlink.data
    assert call.args[0].queue_item.dev_eui == downlink.dev_eui
    assert call.args[0].queue_item.f_port == 2
    assert call.args[0].queue_item.confirmed
    assert call.args[0].queue_item.expires_at.ToDatetime(UTC) == expiry
    assert not call.args[0].flush_queue
    assert not call.args[0].queue_item.is_encrypted
    await connection.close()


@pytest.mark.parametrize("condition", ["offline", "unknown", "moved", "closed"])
async def test_enqueue_scope(connection: ChirpStackConnection, condition: str) -> None:
    prepare(connection)
    if condition == "offline":
        connection.available = False
    elif condition == "unknown":
        connection.devices.clear()
    elif condition == "moved":
        connection._device_api.Get.return_value.device.application_id = "other"
    else:
        await connection.close()
    with pytest.raises(DownlinkError):
        await connection.async_send_downlink(
            Downlink(DESCRIPTOR.dev_eui, 2, b"command")
        )
    connection._device_api.Enqueue.assert_not_awaited()
    await connection.close()


@pytest.mark.parametrize(
    "code,message",
    [
        (grpc.StatusCode.UNAUTHENTICATED, "write permission"),
        (grpc.StatusCode.PERMISSION_DENIED, "write permission"),
        (grpc.StatusCode.UNAVAILABLE, "could not queue"),
    ],
)
async def test_enqueue_error(
    connection: ChirpStackConnection, code: grpc.StatusCode, message: str
) -> None:
    prepare(connection)
    connection._device_api.Enqueue.side_effect = grpc.aio.AioRpcError(
        code, (), (), "secret server details"
    )
    with pytest.raises(DownlinkError, match=message) as error:
        await connection.async_send_downlink(
            Downlink(DESCRIPTOR.dev_eui, 2, b"command")
        )
    assert "secret" not in str(error.value)
    connection._device_api.Enqueue.assert_awaited_once()
    assert connection.available
    await connection.close()


async def test_cancel_does_not_retry(connection: ChirpStackConnection) -> None:
    prepare(connection)
    connection._device_api.Enqueue.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await connection.async_send_downlink(
            Downlink(DESCRIPTOR.dev_eui, 2, b"command")
        )
    connection._device_api.Enqueue.assert_awaited_once()
    await connection.close()
