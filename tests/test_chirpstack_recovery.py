"""Exercise recovery using generated-service response shapes."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import grpc
import pytest
from chirpstack_api import api

from lorawan_connection import ConnectionUnavailable
from lorawan_connection.chirpstack import (
    AuthenticationError,
    ChirpStackConnection,
    connection_error,
)


@pytest.fixture
def connection():
    connection = ChirpStackConnection(
        "http://localhost:8080",
        "secret",
        tenant_id="tenant",
        application_ids=["app"],
        network_id="network",
        poll_interval=0,
        channel=Mock(close=AsyncMock()),
    )
    connection._tenant_api.Get = AsyncMock()
    connection._application_api.List = AsyncMock(
        return_value=api.ListApplicationsResponse(
            total_count=1, result=[api.ApplicationListItem(id="app")]
        )
    )
    connection._device_api.List = AsyncMock(return_value=api.ListDevicesResponse())
    return connection


async def test_poll_recovers(connection):
    error = grpc.aio.AioRpcError(grpc.StatusCode.UNAVAILABLE, (), (), "temporary")
    recovered = asyncio.Event()
    calls = 0

    async def devices(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls in (2, 3):
            raise error
        if calls == 4:
            recovered.set()
        return api.ListDevicesResponse()

    connection._device_api.List.side_effect = devices
    disconnected = Mock()
    connection.on_disconnect(disconnected)
    try:
        await connection.async_connect()
        async with asyncio.timeout(1):
            await recovered.wait()
        assert connection.available
        disconnected.assert_not_called()
    finally:
        await connection.close()


@pytest.mark.parametrize(
    "error,expected,calls",
    [
        (
            grpc.aio.AioRpcError(
                grpc.StatusCode.UNAVAILABLE, (), (), "server unavailable"
            ),
            ConnectionUnavailable,
            3,
        ),
        (
            grpc.aio.AioRpcError(
                grpc.StatusCode.PERMISSION_DENIED, (), (), "access revoked"
            ),
            AuthenticationError,
            1,
        ),
        (ValueError("unexpected device identifier"), ConnectionUnavailable, 1),
    ],
)
async def test_poll_failure_is_observable(connection, error, expected, calls):
    disconnected = asyncio.Event()
    connection.on_disconnect(disconnected.set)
    await connection.async_connect()
    connection._device_api.List.reset_mock()
    connection._device_api.List.side_effect = error
    try:
        async with asyncio.timeout(1):
            await disconnected.wait()
        assert not connection.available
        assert isinstance(connection.error, expected)
        assert connection.error.__cause__ is error
        assert connection._device_api.List.await_count == calls
        assert str(connection.error)
    finally:
        await connection.close()


async def test_listing_restarts_after_count_change(connection):
    connection._application_api.List.side_effect = [
        api.ListApplicationsResponse(
            total_count=2, result=[api.ApplicationListItem(id="old")]
        ),
        api.ListApplicationsResponse(total_count=1),
        api.ListApplicationsResponse(
            total_count=1, result=[api.ApplicationListItem(id="app")]
        ),
    ]
    assert await connection.applications() == {"app": ""}
    assert [
        call.args[0].offset for call in connection._application_api.List.call_args_list
    ] == [0, 1, 0]
    await connection.close()


async def test_close_during_setup(connection):
    started, finish = asyncio.Event(), asyncio.Event()

    async def devices(*args, **kwargs):
        started.set()
        await finish.wait()
        return api.ListDevicesResponse()

    connection._device_api.List.side_effect = devices
    task = asyncio.create_task(connection.async_connect())
    await started.wait()
    await connection.close()
    finish.set()
    with pytest.raises(ConnectionUnavailable):
        await task
    assert not connection.available


@pytest.mark.parametrize(
    "error",
    [
        ConnectionUnavailable("stream closed"),
        grpc.aio.AioRpcError(grpc.StatusCode.INTERNAL, (), (), "redis failed"),
        ValueError("unexpected stream failure"),
    ],
)
async def test_stream_failure_supervision(connection, error):
    from .test_sensecap_example import DESCRIPTOR

    attempts = asyncio.Queue()
    hold = asyncio.Event()

    async def stream(*args, **kwargs):
        attempts.put_nowait(True)
        if attempts.qsize() == 1:
            raise error
        await hold.wait()
        yield

    connection.devices = {DESCRIPTOR.dev_eui: DESCRIPTOR}
    connection.available = True
    connection._internal_api.StreamDeviceEvents = Mock(side_effect=stream)
    task = asyncio.create_task(connection._stream(DESCRIPTOR.dev_eui))
    try:
        await asyncio.sleep(0)
        if isinstance(error, ValueError):
            await task
            assert not connection.available
            assert connection.error.__cause__ is error
        else:
            async with asyncio.timeout(2):
                while attempts.qsize() < 2:
                    await asyncio.sleep(0.01)
            assert connection.available
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await connection.close()


def test_failure_retains_reason():
    error = ConnectionUnavailable("Device stream disappeared")
    assert connection_error(error) is error


async def test_repeated_stream_failure_disconnects(connection):
    from .test_sensecap_example import DESCRIPTOR

    async def stream(*args, **kwargs):
        if False:
            yield

    connection.devices = {DESCRIPTOR.dev_eui: DESCRIPTOR}
    connection.available = True
    disconnected = Mock()
    connection.on_disconnect(disconnected)
    connection._internal_api.StreamDeviceEvents = Mock(side_effect=stream)
    with patch("lorawan_connection.chirpstack.asyncio.sleep", AsyncMock()):
        await connection._stream(DESCRIPTOR.dev_eui)
    assert connection._internal_api.StreamDeviceEvents.call_count == 3
    assert not connection.available
    assert "stream closed" in str(connection.error)
    disconnected.assert_called_once()
    await connection.close()


async def test_removed_stream_not_found_refreshes(connection):
    from .test_sensecap_example import DESCRIPTOR

    async def stream(*args, **kwargs):
        raise grpc.aio.AioRpcError(grpc.StatusCode.NOT_FOUND, (), (), "Device removed")
        yield

    connection.devices = {DESCRIPTOR.dev_eui: DESCRIPTOR}
    connection.available = True
    connection._internal_api.StreamDeviceEvents = Mock(side_effect=stream)
    await connection._stream(DESCRIPTOR.dev_eui)
    assert connection.devices == {}
    assert connection.available
    await connection.close()
