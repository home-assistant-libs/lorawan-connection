"""Test scoped inventory, stream decoding, and connection failure contracts."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import grpc
import pytest
from chirpstack_api import api, integration
from google.protobuf.json_format import MessageToJson
from google.protobuf.message import Message

from lorawan_connection import DeviceEventData, EventType
from lorawan_connection.chirpstack import (
    AuthenticationError,
    ChirpStackConnection,
    ConnectionUnavailable,
    connection_error,
)

from .test_sensecap_example import DESCRIPTOR, PAYLOAD


@pytest.fixture
def connection() -> ChirpStackConnection:
    """Create a transport with a mocked gRPC channel."""
    return ChirpStackConnection(
        "http://localhost:8080",
        "secret",
        "tenant",
        ["application"],
        "network",
        channel=Mock(close=AsyncMock()),
    )


async def test_inventory_and_unknown_order(connection: ChirpStackConnection) -> None:
    """Unknown activity triggers one refresh and descriptors precede activity."""
    callback = Mock()
    with (
        patch.object(connection, "_snapshot", AsyncMock(return_value={})),
        patch.object(connection, "_stream", AsyncMock()),
    ):
        stop = await connection.async_subscribe(callback, Mock())
        with patch.object(
            connection,
            "_snapshot",
            AsyncMock(return_value={DESCRIPTOR.dev_eui: DESCRIPTOR}),
        ) as snapshot:
            event = DeviceEventData(
                "network",
                DESCRIPTOR.dev_eui,
                EventType.UPLINK,
                datetime.now(UTC),
                data=integration.UplinkEvent(data=PAYLOAD, f_port=1),
            )
            await asyncio.gather(
                connection.handle_activity(event), connection.handle_activity(event)
            )
            snapshot.assert_awaited_once()
        assert [call.args[0].type for call in callback.call_args_list] == [
            EventType.ADDED,
            EventType.UPLINK,
            EventType.UPLINK,
        ]
        stop()
        stop()
    await connection.close()


async def test_snapshot_failure_preserves_inventory(
    connection: ChirpStackConnection,
) -> None:
    """An incomplete refresh must never invent removals."""
    connection.devices = {DESCRIPTOR.dev_eui: DESCRIPTOR}
    with (
        patch.object(
            connection, "_snapshot", AsyncMock(side_effect=ConnectionUnavailable)
        ),
        pytest.raises(ConnectionUnavailable),
    ):
        await connection.refresh()
    assert connection.devices == {DESCRIPTOR.dev_eui: DESCRIPTOR}
    await connection.close()


async def test_inventory_changes(connection: ChirpStackConnection) -> None:
    """Emit only changes and stop a removed device's stream."""
    callback = Mock()
    with (
        patch.object(
            connection,
            "_snapshot",
            AsyncMock(return_value={DESCRIPTOR.dev_eui: DESCRIPTOR}),
        ) as snapshot,
        patch.object(connection, "_stream", AsyncMock()),
    ):
        await connection.async_subscribe(callback, Mock())
        await connection.refresh()
        assert callback.call_count == 1
        snapshot.return_value = {
            DESCRIPTOR.dev_eui: replace(DESCRIPTOR, name="changed")
        }
        await connection.refresh()
        snapshot.return_value = {}
        await connection.refresh()
    assert [call.args[0].type for call in callback.call_args_list] == [
        EventType.ADDED,
        EventType.UPDATED,
        EventType.REMOVED,
    ]
    await connection.close()


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        (EventType.UPLINK, integration.UplinkEvent(data=PAYLOAD, f_port=1)),
        (EventType.JOIN, integration.JoinEvent(dev_addr="01020304")),
        (
            EventType.STATUS,
            integration.StatusEvent(battery_level=0, battery_level_unavailable=False),
        ),
        (EventType.ACK, integration.AckEvent(acknowledged=True)),
        (EventType.TX_ACK, integration.TxAckEvent(gateway_id="0101010101010101")),
        (EventType.LOG, integration.LogEvent(description="Example")),
        (EventType.LOCATION, integration.LocationEvent()),
    ],
)
async def test_stream_decode_replay_and_disconnect(
    connection: ChirpStackConnection,
    kind: EventType,
    message: Message,
) -> None:
    """Keep generated payloads, drop backlog, and fail once after EOF."""
    now = datetime.now(UTC)
    message.device_info.dev_eui = DESCRIPTOR.dev_eui
    live = api.LogItem(
        id=f"{int((now + timedelta(seconds=1)).timestamp() * 1000)}-0",
        description=kind,
        body=MessageToJson(message),
    )
    old = api.LogItem(id="1-0", description=kind, body=MessageToJson(message))

    async def stream() -> object:
        for item in (
            old,
            api.LogItem(id=live.id, description=kind, body="bad json"),
            live,
        ):
            yield item

    callback, disconnected = Mock(), Mock()
    connection._callback = callback
    connection._on_disconnect = disconnected
    connection.available = True
    connection.devices = {DESCRIPTOR.dev_eui: DESCRIPTOR}
    connection._internal_api.StreamDeviceEvents = Mock(return_value=stream())
    await connection._stream(DESCRIPTOR.dev_eui)
    callback.assert_called_once()
    event = callback.call_args.args[0]
    assert event.type == kind
    assert event.data == message
    assert not connection.available
    disconnected.assert_called_once()
    connection._failed(ConnectionUnavailable())
    disconnected.assert_called_once()
    await connection.close()


async def test_catalog_resolution(connection: ChirpStackConnection) -> None:
    """Resolve the profile's catalog model and Alliance vendor ID."""
    connection.applications = AsyncMock(return_value={"application": "Test"})
    connection._device_api.List = AsyncMock(
        return_value=api.ListDevicesResponse(
            total_count=1,
            result=[
                api.DeviceListItem(
                    dev_eui=DESCRIPTOR.dev_eui,
                    name="Greenhouse",
                    device_profile_id="profile",
                )
            ],
        )
    )
    connection._profile_api.Get = AsyncMock(
        return_value=api.GetDeviceProfileResponse(
            device_profile=api.DeviceProfile(device_id=DESCRIPTOR.catalog_model_id)
        )
    )
    connection._profile_api.GetDevice = AsyncMock(
        return_value=api.GetDeviceProfileDeviceResponse(
            device=api.DeviceProfileDevice(vendor_id="vendor", name="S2101")
        )
    )
    connection._profile_api.GetVendor = AsyncMock(
        return_value=api.GetDeviceProfileVendorResponse(
            vendor=api.DeviceProfileVendor(vendor_id=744, name="Seeed")
        )
    )
    result = await connection._snapshot()
    assert result[DESCRIPTOR.dev_eui].vendor_id == 744
    assert result[DESCRIPTOR.dev_eui].catalog_model_id == DESCRIPTOR.catalog_model_id
    await connection.close()


async def test_pagination_and_scope(connection: ChirpStackConnection) -> None:
    """Honor all pages and reject a tenant/application scope mismatch."""
    method = AsyncMock(
        side_effect=[
            api.ListDevicesResponse(
                total_count=2, result=[api.DeviceListItem(dev_eui="1")]
            ),
            api.ListDevicesResponse(total_count=2),
        ]
    )
    with pytest.raises(ConnectionUnavailable, match="Incomplete"):
        await connection._list(method, api.ListDevicesRequest)
    assert method.call_args.args[0].offset == 1
    connection.applications = AsyncMock(return_value={"other": "Other"})
    with pytest.raises(ConnectionUnavailable, match="tenant"):
        await connection._snapshot()
    await connection.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "localhost:8080",
        "ftp://example.org",
        "https://user:secret@example.org",
        "https://example.org/path",
    ],
)
def test_endpoint_validation(endpoint: str) -> None:
    """TLS failures never cause automatic plaintext fallback."""
    with pytest.raises(ValueError):
        ChirpStackConnection(endpoint, "secret", "tenant", [], "network")


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.UNAUTHENTICATED, AuthenticationError),
        (grpc.StatusCode.PERMISSION_DENIED, ConnectionUnavailable),
        (grpc.StatusCode.UNAVAILABLE, ConnectionUnavailable),
    ],
)
def test_error_classification(code: grpc.StatusCode, expected: type[Exception]) -> None:
    """Missing scope must not trigger a false invalid-credential claim."""
    error = grpc.aio.AioRpcError(code, (), (), "test")
    assert isinstance(connection_error(error), expected)


async def test_setup_authentication_failure_closes_channel(
    connection: ChirpStackConnection,
) -> None:
    """No channel survives a rejected initial subscription."""
    error = grpc.aio.AioRpcError(grpc.StatusCode.UNAUTHENTICATED, (), (), "")
    with (
        patch.object(connection, "_snapshot", AsyncMock(side_effect=error)),
        pytest.raises(AuthenticationError),
    ):
        await connection.async_subscribe(Mock(), Mock())
    connection.channel.close.assert_awaited_once()
    assert not connection.available


async def test_no_false_removal_when_page_count_changes(
    connection: ChirpStackConnection,
) -> None:
    """Reject a visibly inconsistent multi-page snapshot."""
    method = AsyncMock(
        side_effect=[
            api.ListDevicesResponse(
                total_count=2, result=[api.DeviceListItem(dev_eui="1")]
            ),
            api.ListDevicesResponse(total_count=1),
        ]
    )
    with pytest.raises(ConnectionUnavailable, match="changed"):
        await connection._list(method, api.ListDevicesRequest)
    await connection.close()


async def test_tls_channel_verifies_by_default() -> None:
    """Secure endpoints must use TLS credentials, without fallback."""
    with (
        patch(
            "grpc.aio.secure_channel", return_value=Mock(close=AsyncMock())
        ) as secure,
        patch("grpc.aio.insecure_channel") as insecure,
    ):
        connection = ChirpStackConnection(
            "https://example.org:8080", "key", "tenant", [], "network"
        )
        secure.assert_called_once()
        insecure.assert_not_called()
        await connection.close()


async def test_inventory_does_not_open_streams(
    connection: ChirpStackConnection,
) -> None:
    with (
        patch.object(
            connection,
            "_snapshot",
            AsyncMock(return_value={DESCRIPTOR.dev_eui: DESCRIPTOR}),
        ),
        patch.object(connection, "_stream", AsyncMock()) as stream,
    ):
        assert await connection.inventory() == (DESCRIPTOR,)
        stream.assert_not_called()
    assert connection.devices == {}
    assert not connection.available
    await connection.close()


async def test_cancel_initial_subscription_closes_channel(
    connection: ChirpStackConnection,
) -> None:
    with (
        patch.object(
            connection, "_snapshot", AsyncMock(side_effect=asyncio.CancelledError)
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await connection.async_subscribe(Mock(), Mock())
    connection.channel.close.assert_awaited_once()


async def test_unsubscribe_does_not_allow_second_subscription(
    connection: ChirpStackConnection,
) -> None:
    with patch.object(connection, "_snapshot", AsyncMock(return_value={})):
        stop = await connection.async_subscribe(Mock(), Mock())
        stop()
        with pytest.raises(ConnectionUnavailable):
            await connection.async_subscribe(Mock(), Mock())
    await connection.close()
