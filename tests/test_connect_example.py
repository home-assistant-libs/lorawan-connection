"""Run the exact connection script embedded in the documentation."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

import connect_sensors
from lorawan_connection import DeviceEvent, DeviceEventData, EventType, UplinkData
from lorawan_connection.chirpstack import ConnectionUnavailable
from sensecap_lorawan import S2101

from .test_sensecap_example import DESCRIPTOR, PAYLOAD


@pytest.fixture
def connection(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setenv("CHIRPSTACK_SERVER", "https://example.com:443")
    monkeypatch.setenv("CHIRPSTACK_API_KEY", "test-key")
    monkeypatch.setenv("CHIRPSTACK_TENANT_ID", "tenant")
    connection = Mock(
        network_id="my-network",
        applications=AsyncMock(return_value={"application": "Sensors"}),
        async_subscribe=AsyncMock(),
        async_connect=AsyncMock(),
        error=None,
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        connect_sensors, "ChirpStackConnection", Mock(return_value=connection)
    )
    return connection


async def test_connection_example(
    connection: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inventory, readings, disconnect and cleanup use the real device library."""
    descriptor = replace(DESCRIPTOR, network_id=connection.network_id)
    stop = Mock()
    models: list[S2101] = []

    async def subscribe(
        callback: Callable[[DeviceEvent], None],
        *,
        vendor_ids: frozenset[int],
    ) -> Mock:
        devices = callback.__self__
        devices.subscribe_device_added(models.append)
        now = datetime.now(UTC)
        callback(
            DeviceEventData(
                descriptor.network_id,
                descriptor.dev_eui,
                EventType.ADDED,
                now,
                descriptor,
            )
        )
        callback(
            DeviceEventData(
                descriptor.network_id,
                descriptor.dev_eui,
                EventType.UPLINK,
                now,
                data=UplinkData(PAYLOAD),
            )
        )
        connection.error = ConnectionUnavailable("Disconnected")
        connection.on_disconnect.call_args.args[0]()
        return stop

    connection.async_subscribe.side_effect = subscribe
    with pytest.raises(ConnectionUnavailable, match="Disconnected"):
        await connect_sensors.main()

    output = capsys.readouterr().out
    assert "Device: Greenhouse" in output
    assert "temperature=21.4, humidity=31.4" in output
    assert connection.application_ids == ["application"]
    assert models[0].closed
    stop.assert_called_once_with()
    connection.close.assert_awaited_once_with()


async def test_connection_example_cancelled(connection: Mock) -> None:
    """Cancellation, including Ctrl+C through asyncio.run, closes resources."""
    subscribed = asyncio.Event()
    stop = Mock()

    async def subscribe(**kwargs: object) -> Mock:
        subscribed.set()
        return stop

    connection.async_subscribe.side_effect = subscribe
    task = asyncio.create_task(connect_sensors.main())
    await subscribed.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stop.assert_called_once_with()
    connection.close.assert_awaited_once_with()


@pytest.mark.parametrize("method", ["applications", "async_subscribe"])
async def test_connection_example_setup_failure(connection: Mock, method: str) -> None:
    """A failed discovery or initial subscription still closes the connection."""
    getattr(connection, method).side_effect = ConnectionUnavailable("Setup failed")
    with pytest.raises(ConnectionUnavailable, match="Setup failed"):
        await connect_sensors.main()
    connection.close.assert_awaited_once_with()
