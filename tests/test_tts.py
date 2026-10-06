"""The Things Stack adapter contracts using the official wire definitions."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import grpc
import pytest

from dragino_lorawan import LT22222, DraginoDevices
from lorawan_connection import ConnectionUnavailable, Downlink, DownlinkError, EventType
from lorawan_connection.backend._tts_api import message
from lorawan_connection.backend.tts import AuthenticationError, TTSConnection
from sensecap_lorawan import SenseCapDeviceCollection

EUI = "0201010101010101"
IDS = {
    "application_ids": {"application_id": "app"},
    "device_id": "sensor",
    "dev_eui": bytes.fromhex(EUI),
}


def device(name: str = "Greenhouse"):
    return message(
        "EndDevice",
        ids=IDS,
        name=name,
        version_ids={
            "brand_id": "sensecap",
            "model_id": "sensecaps2101-temp-humid",
        },
    )


@pytest.fixture
async def connection() -> AsyncIterator[TTSConnection]:
    connection = TTSConnection(
        "http://localhost:1", "secret", application_ids=["app"], network_id="network"
    )
    connection._registry.List = AsyncMock(
        return_value=message("EndDevices", end_devices=[device()])
    )
    connection._access.ListRights = AsyncMock(
        return_value=message("Rights", rights=["RIGHT_APPLICATION_TRAFFIC_READ"])
    )
    await connection.inventory()
    connection.available = True
    yield connection
    await connection.close()


async def test_uplink_and_catalog_model(connection: TTSConnection) -> None:
    models = SenseCapDeviceCollection(connection)
    await models.async_setup()
    model = models.devices[EUI]
    await connection.handle_message(
        message(
            "ApplicationUp",
            end_device_ids=IDS,
            uplink_message={
                "f_port": 1,
                "frm_payload": bytes.fromhex("01011098530000010210A87A0000AF51"),
            },
        )
    )
    assert model.temperature == 21.4
    assert model.humidity == 31.4
    models.close()


async def test_snapshot_failure_preserves_inventory(connection: TTSConnection) -> None:
    connection.application_ids.append("second")
    connection._registry.List = AsyncMock(
        side_effect=[
            message("EndDevices"),
            grpc.aio.AioRpcError(grpc.StatusCode.UNAVAILABLE, (), (), "offline"),
        ]
    )
    with pytest.raises(ConnectionUnavailable):
        await connection.inventory()
    assert list(connection.devices) == [EUI]


async def test_duplicate_eui_preserves_inventory(connection: TTSConnection) -> None:
    connection._registry.List = AsyncMock(
        return_value=message("EndDevices", end_devices=[device(), device()])
    )
    with pytest.raises(ConnectionUnavailable, match="Duplicate"):
        await connection.refresh()
    assert list(connection.devices) == [EUI]


async def test_missing_traffic_right(connection: TTSConnection) -> None:
    connection._access.ListRights = AsyncMock(return_value=message("Rights"))
    with pytest.raises(AuthenticationError, match="traffic read"):
        await connection.inventory()


@pytest.mark.parametrize(
    "code", [grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED]
)
async def test_authentication_error(
    connection: TTSConnection, code: grpc.StatusCode
) -> None:
    connection._registry.List = AsyncMock(
        side_effect=grpc.aio.AioRpcError(code, (), (), "secret")
    )
    with pytest.raises(AuthenticationError, match="read permissions"):
        await connection.inventory()


async def test_inventory_reconciliation(connection: TTSConnection) -> None:
    events = []
    unsubscribe = await connection.async_subscribe(
        brands=frozenset({("tts", "sensecap")}), callback=events.append
    )
    connection._registry.List = AsyncMock(
        return_value=message("EndDevices", end_devices=[device("Renamed")])
    )
    await connection.refresh()
    connection._registry.List = AsyncMock(return_value=message("EndDevices"))
    await connection.refresh()
    assert [event.type for event in events] == [
        EventType.ADDED,
        EventType.UPDATED,
        EventType.REMOVED,
    ]
    unsubscribe()
    unsubscribe()


async def test_unknown_device_refresh_before_uplink(connection: TTSConnection) -> None:
    connection.devices.clear()
    events = []
    await connection.async_subscribe(brands=None, callback=events.append)
    await connection.handle_message(
        message(
            "ApplicationUp",
            end_device_ids=IDS,
            uplink_message={"frm_payload": b"payload", "f_port": 1},
        )
    )
    assert [event.type for event in events] == [EventType.ADDED, EventType.UPLINK]


@pytest.mark.parametrize(
    "field,ack", [("downlink_ack", True), ("downlink_nack", False)]
)
async def test_ack_correlation(
    connection: TTSConnection, field: str, ack: bool
) -> None:
    events = []
    await connection.async_subscribe(brands=None, callback=events.append)
    await connection.handle_message(
        message(
            "ApplicationUp",
            end_device_ids=IDS,
            **{field: {"correlation_ids": ["other", "lorawan-connection:test"]}},
        )
    )
    assert events[-1].queue_item_id == "lorawan-connection:test"
    assert events[-1].acknowledged is ack


async def test_downlink_failure_fails_wait(connection: TTSConnection) -> None:
    models = SenseCapDeviceCollection(connection)
    await models.async_setup()
    connection._application.DownlinkQueuePush = AsyncMock()
    command = asyncio.create_task(
        models.devices[EUI].async_send_downlink(data=b"x", f_port=1)
    )
    await asyncio.sleep(0)
    queued = connection._application.DownlinkQueuePush.call_args.args[0].downlinks[0]
    await connection.handle_message(
        message(
            "ApplicationUp",
            end_device_ids=IDS,
            downlink_failed={"downlink": {"correlation_ids": queued.correlation_ids}},
        )
    )
    with pytest.raises(DownlinkError):
        await command
    models.close()


async def test_encrypted_payload_rejected(connection: TTSConnection) -> None:
    with pytest.raises(ConnectionUnavailable, match="encrypted"):
        await connection.handle_message(
            message(
                "ApplicationUp",
                end_device_ids=IDS,
                uplink_message={"app_s_key": {"key": bytes(16)}},
            )
        )


async def test_unrelated_application_ignored(connection: TTSConnection) -> None:
    events = []
    await connection.async_subscribe(brands=None, callback=events.append)
    ids = {**IDS, "application_ids": {"application_id": "another"}}
    await connection.handle_message(
        message("ApplicationUp", end_device_ids=ids, uplink_message={"f_port": 1})
    )
    assert len(events) == 1


async def test_queue_expiry_rejected(connection: TTSConnection) -> None:
    connection._application.DownlinkQueuePush = AsyncMock()
    with pytest.raises(DownlinkError, match="expiry"):
        await connection.async_send_downlink(
            Downlink(EUI, 1, b"data", expires_at=datetime.now(UTC))
        )
    connection._application.DownlinkQueuePush.assert_not_called()


async def test_permission_denied_write_keeps_connection(
    connection: TTSConnection,
) -> None:
    connection._application.DownlinkQueuePush = AsyncMock(
        side_effect=grpc.aio.AioRpcError(
            grpc.StatusCode.PERMISSION_DENIED, (), (), "denied"
        )
    )
    with pytest.raises(DownlinkError, match="PERMISSION_DENIED"):
        await connection.async_send_downlink(Downlink(EUI, 1, b"data"))
    assert connection.available


async def test_disconnect_fails_commands_once(connection: TTSConnection) -> None:
    models = SenseCapDeviceCollection(connection)
    await models.async_setup()
    connection._application.DownlinkQueuePush = AsyncMock()
    observer = Mock()
    connection.on_disconnect(observer)
    command = asyncio.create_task(
        models.devices[EUI].async_send_downlink(data=b"data", f_port=1)
    )
    await asyncio.sleep(0)
    connection._failed(ConnectionUnavailable("offline"))
    connection._failed(ConnectionUnavailable("offline again"))
    with pytest.raises(DownlinkError):
        await command
    observer.assert_called_once_with()
    assert not models.devices[EUI].closed
    models.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "mqtt://host",
        "https://user:secret@host",
        "https://host/path",
        "https://host:99999",
    ],
)
def test_invalid_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError):
        TTSConnection(endpoint, "key", application_ids=["app"], network_id="test")


async def test_independent_endpoints() -> None:
    connection = TTSConnection(
        "http://localhost:1",
        "key",
        identity_server="http://localhost:2",
        application_ids=["app"],
        network_id="test",
    )
    assert connection.channel is not connection._identity_channel
    await connection.close()


async def test_setup_failure_closes_both_channels(connection: TTSConnection) -> None:
    channels = [Mock(channel_ready=AsyncMock(), close=AsyncMock()) for _ in range(2)]
    old_channels = connection._channels
    connection._channels = channels
    connection.inventory = AsyncMock(side_effect=AuthenticationError("bad key"))
    try:
        with pytest.raises(AuthenticationError):
            await connection.async_connect()
        assert not connection.available
        assert connection._closed
        for channel in channels:
            channel.close.assert_awaited_once()
    finally:
        for channel in old_channels:
            await channel.close()


async def test_lifecycle_stream_end_disconnects(connection: TTSConnection) -> None:
    class Stream:
        initial_metadata = AsyncMock()

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    connection._events.Stream = Mock(return_value=Stream())
    disconnected = Mock()
    connection.on_disconnect(disconnected)
    await connection._lifecycle()
    assert isinstance(connection.error, ConnectionUnavailable)
    assert not connection.available
    disconnected.assert_called_once_with()


@pytest.mark.parametrize(
    "field,expected",
    [
        ("join_accept", EventType.JOIN),
        ("location_solved", EventType.LOCATION),
    ],
)
async def test_other_application_events(
    connection: TTSConnection, field: str, expected: EventType
) -> None:
    received = []
    await connection.async_subscribe(brands=None, callback=received.append)
    fields = {
        "join_accept": {"session_key_id": b"test"},
        "location_solved": {
            "location": {"latitude": 42, "longitude": 4, "altitude": 10}
        },
    }
    await connection.handle_message(
        message("ApplicationUp", end_device_ids=IDS, **{field: fields[field]})
    )
    assert received[-1].type is expected


@pytest.mark.parametrize(
    "method,payload",
    [
        ("async_set_relay", b"\x03\x01\x11"),
        ("async_set_digital_output", b"\x02\x01\x11\x11"),
    ],
)
async def test_dragino_output_command(
    connection: TTSConnection, method: str, payload: bytes
) -> None:
    connection.devices[EUI] = replace(
        connection.devices[EUI],
        brand_id=LT22222.identifiers["tts"][0],
        model_id=LT22222.identifiers["tts"][1],
    )
    models = DraginoDevices(connection)
    await models.async_setup()
    model = models.devices[EUI]
    connection._application.DownlinkQueuePush = AsyncMock()
    pending = asyncio.create_task(getattr(model, method)(1, True))
    await asyncio.sleep(0)
    queued = connection._application.DownlinkQueuePush.call_args.args[0].downlinks[0]
    assert queued.frm_payload == payload
    assert queued.f_port == 2
    assert queued.confirmed
    await connection.handle_message(
        message(
            "ApplicationUp",
            end_device_ids=IDS,
            downlink_ack={"correlation_ids": queued.correlation_ids},
        )
    )
    await pending
    assert model.relays == {1: None, 2: None}
    assert model.digital_outputs == {1: None, 2: None}
    models.close()


@pytest.mark.parametrize("endpoint", ["http://localhost", "https://localhost"])
async def test_shared_channel(endpoint: str) -> None:
    """Equal default and explicit ports use one channel and close it once."""
    from unittest.mock import patch

    channel = Mock(close=AsyncMock())
    with (
        patch("grpc.aio.insecure_channel", return_value=channel),
        patch("grpc.aio.secure_channel", return_value=channel),
    ):
        connection = TTSConnection(
            endpoint,
            "key",
            identity_server=endpoint
            + (":443" if endpoint.startswith("https") else ":80"),
            application_ids=["app"],
            network_id="test",
        )
    assert connection.channel is connection._identity_channel
    assert len(connection._channels) == 1
    await connection.close()
    channel.close.assert_awaited_once()


async def test_overlapping_refreshes(connection: TTSConnection) -> None:
    """Requests during a scan share a follow-up that sees changes missed by it."""
    started = asyncio.Event()
    release = asyncio.Event()
    response = message("EndDevices", end_devices=[device()])

    async def scan(*args, **kwargs):
        snapshot = response
        started.set()
        await release.wait()
        return snapshot

    connection._registry.List = AsyncMock(side_effect=scan)
    first = asyncio.create_task(connection.refresh())
    await started.wait()
    response = message("EndDevices", end_devices=[device("Renamed")])
    waiting = [asyncio.create_task(connection.refresh()) for _ in range(3)]
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, *waiting)
    assert connection._registry.List.await_count == 2
    assert connection.devices[EUI].name == "Renamed"


async def test_failed_refresh_does_not_satisfy_waiters(
    connection: TTSConnection,
) -> None:
    """A queued refresh retries after a failed snapshot without losing inventory."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def fail(*args, **kwargs):
        started.set()
        await release.wait()
        raise ConnectionUnavailable("offline")

    connection._registry.List = AsyncMock(side_effect=fail)
    first = asyncio.create_task(connection.refresh())
    await started.wait()
    second = asyncio.create_task(connection.refresh())
    await asyncio.sleep(0)
    connection._registry.List.side_effect = None
    connection._registry.List.return_value = message(
        "EndDevices", end_devices=[device("Recovered")]
    )
    release.set()
    with pytest.raises(ConnectionUnavailable):
        await first
    await second
    assert connection.devices[EUI].name == "Recovered"
    assert connection._registry.List.await_count == 2


class QueueStream:
    """Control gRPC stream delivery without a network or timers."""

    def __init__(self):
        self.queue = asyncio.Queue()

    async def initial_metadata(self):
        return ()

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.queue.get()
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise StopAsyncIteration
        return value


async def test_connect_streams_reconcile_and_close(connection: TTSConnection) -> None:
    """Startup waits for subscription, reconciles changes and routes live traffic."""
    from unittest.mock import patch

    lifecycle, traffic = QueueStream(), QueueStream()
    connection._events.Stream = Mock(return_value=lifecycle)
    connection._application.Subscribe = Mock(return_value=traffic)
    lifecycle.queue.put_nowait(message("Event", name="events.stream.start"))
    with patch.object(connection.channel, "channel_ready", new=AsyncMock()):
        await connection.async_connect()
    assert connection.available
    with pytest.raises(ConnectionUnavailable, match="already started"):
        await connection.async_connect()
    events = []
    updated = asyncio.Event()

    def receive(event):
        events.append(event)
        updated.set()

    await connection.async_subscribe(brands=None, callback=receive)
    updated.clear()
    connection._registry.List.return_value = message(
        "EndDevices", end_devices=[device("Renamed")]
    )
    lifecycle.queue.put_nowait(message("Event", name="end_device.update"))
    await asyncio.wait_for(updated.wait(), 1)
    assert events[-1].descriptor.name == "Renamed"
    updated.clear()
    traffic.queue.put_nowait(
        message(
            "ApplicationUp",
            end_device_ids=IDS,
            uplink_message={"frm_payload": b"hello", "f_port": 1},
        )
    )
    await asyncio.wait_for(updated.wait(), 1)
    assert events[-1].data == b"hello"
    disconnect = Mock()
    connection.on_disconnect(disconnect)
    await connection.close()
    disconnect.assert_not_called()
    with pytest.raises(ConnectionUnavailable):
        await connection.async_subscribe(brands=None, callback=receive)
    with pytest.raises(ConnectionUnavailable):
        connection.on_disconnect(disconnect)
    with pytest.raises(DownlinkError, match="unavailable"):
        await connection.async_send_downlink(Downlink(EUI, 1, b"x"))


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        grpc.aio.AioRpcError(grpc.StatusCode.UNAVAILABLE, (), (), "offline"),
    ],
)
async def test_channel_startup_failure(
    connection: TTSConnection, error: Exception
) -> None:
    from unittest.mock import patch

    with patch.object(
        connection.channel, "channel_ready", new=AsyncMock(side_effect=error)
    ):
        with pytest.raises(ConnectionUnavailable):
            await connection.async_connect()
    assert connection._closed
    assert not connection.available


@pytest.mark.parametrize("fail_after_ready", [False, True])
async def test_stream_failure_during_startup(
    connection: TTSConnection, fail_after_ready: bool
) -> None:
    from unittest.mock import patch

    async def lifecycle():
        connection._ready.set()
        if not fail_after_ready:
            connection._failed(ConnectionUnavailable("stream failed"))

    async def reconcile():
        connection._failed(ConnectionUnavailable("stream failed"))

    with (
        patch.object(connection.channel, "channel_ready", new=AsyncMock()),
        patch.object(connection, "inventory", new=AsyncMock()),
        patch.object(connection, "_lifecycle", new=lifecycle),
        patch.object(connection, "_traffic", new=AsyncMock()),
        patch.object(connection, "refresh", new=reconcile),
    ):
        with pytest.raises(ConnectionUnavailable, match="stream failed"):
            await connection.async_connect()
    assert connection._closed


async def test_traffic_stream_end_disconnects(connection: TTSConnection) -> None:
    stream = QueueStream()
    stream.queue.put_nowait(None)
    connection._application.Subscribe = Mock(return_value=stream)
    await connection._traffic("app")
    assert isinstance(connection.error, ConnectionUnavailable)
    assert not connection.available


async def test_poll_failure_disconnects(connection: TTSConnection) -> None:
    from unittest.mock import patch

    connection.poll_interval = 0
    with patch.object(
        connection,
        "refresh",
        new=AsyncMock(side_effect=ConnectionUnavailable("offline")),
    ):
        await connection._poll()
    assert not connection.available


@pytest.mark.parametrize(
    "options",
    [
        {"application_ids": []},
        {"application_ids": [" "]},
        {"application_ids": ["app", "app"]},
        {"poll_interval": 0},
    ],
)
def test_invalid_options(options) -> None:
    with pytest.raises(ValueError):
        TTSConnection(
            "http://localhost:1",
            "key",
            network_id="test",
            **{"application_ids": ["app"], **options},
        )


@pytest.mark.parametrize(
    "up",
    [
        message("ApplicationUp", end_device_ids={**IDS, "dev_eui": b""}),
        message(
            "ApplicationUp",
            end_device_ids={**IDS, "device_id": "different"},
            uplink_message={"f_port": 1},
        ),
        message("ApplicationUp", end_device_ids=IDS, location_solved={}),
        message(
            "ApplicationUp",
            end_device_ids=IDS,
            downlink_ack={"correlation_ids": ["unrelated"]},
        ),
        message("ApplicationUp", end_device_ids=IDS),
    ],
)
async def test_irrelevant_traffic(connection: TTSConnection, up) -> None:
    events = []
    await connection.async_subscribe(brands=None, callback=events.append)
    await connection.handle_message(up)
    assert len(events) == 1


async def test_pagination_and_missing_eui(connection: TTSConnection) -> None:
    connection._registry.List.side_effect = [
        message(
            "EndDevices",
            end_devices=[
                message("EndDevice", ids={"device_id": str(index)})
                for index in range(100)
            ],
        ),
        message("EndDevices", end_devices=[device()]),
    ]
    await connection.refresh()
    assert list(connection.devices) == [EUI]
    assert connection._registry.List.call_args.args[0].page == 2


async def test_unknown_device_stays_absent(connection: TTSConnection) -> None:
    connection._registry.List.return_value = message("EndDevices")
    connection.devices.clear()
    await connection.handle_message(
        message("ApplicationUp", end_device_ids=IDS, uplink_message={"f_port": 1})
    )
    with pytest.raises(DownlinkError, match="no longer exists"):
        await connection.async_send_downlink(Downlink(EUI, 1, b"x"))
