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
        tenant_id="tenant",
        application_ids=["application"],
        network_id="network",
        channel=Mock(close=AsyncMock()),
    )


async def test_inventory_and_unknown_order(connection: ChirpStackConnection) -> None:
    """Unknown activity triggers one refresh and descriptors precede activity."""
    callback = Mock()
    with (
        patch.object(connection, "_snapshot", AsyncMock(return_value={})),
        patch.object(connection, "_stream", AsyncMock()),
    ):
        await connection.async_connect()
        stop = await connection.async_subscribe(vendor_ids=None, callback=callback)
        with patch.object(
            connection,
            "_snapshot",
            AsyncMock(return_value={DESCRIPTOR.dev_eui: DESCRIPTOR}),
        ) as snapshot:
            event = DeviceEventData(
                network_id="network",
                dev_eui=DESCRIPTOR.dev_eui,
                type=EventType.UPLINK,
                received_at=datetime.now(UTC),
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
        await connection.async_connect()
        await connection.async_subscribe(vendor_ids=None, callback=callback)
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
@pytest.mark.parametrize("server_offset", [-60, 60])
async def test_stream_decode_replay_and_disconnect(
    connection: ChirpStackConnection,
    kind: EventType,
    message: Message,
    server_offset: int,
) -> None:
    """Keep server payloads and timestamps, and fail once after EOF."""
    now = datetime.now(UTC)
    message.device_info.dev_eui = DESCRIPTOR.dev_eui
    live = api.LogItem(
        id=f"{int((now + timedelta(seconds=server_offset)).timestamp() * 1000)}-0",
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
    connection.on_disconnect(disconnected)
    connection.available = True
    await connection.async_subscribe(vendor_ids=None, callback=callback)
    connection.devices = {DESCRIPTOR.dev_eui: DESCRIPTOR}
    connection._internal_api.StreamDeviceEvents = Mock(return_value=stream())
    await connection._stream(DESCRIPTOR.dev_eui)
    assert callback.call_count == 2
    assert callback.call_args_list[0].args[0].received_at == datetime.fromtimestamp(
        0.001, UTC
    )
    event = callback.call_args.args[0]
    assert event.type == kind
    assert event.data == message
    assert event.received_at == datetime.fromtimestamp(
        int(live.id.split("-", 1)[0]) / 1000, UTC
    )
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


@pytest.mark.parametrize("tenant_options", [{}, {"tenant_id": None}])
async def test_applications_across_tenants(
    tenant_options: dict[str, str | None],
) -> None:
    """Collect all pages of tenants and their applications without losing namesakes."""
    connection = ChirpStackConnection(
        "http://localhost:8080",
        "secret",
        application_ids=[],
        network_id="network",
        channel=Mock(close=AsyncMock()),
        **tenant_options,
    )
    assert connection.tenant_id is None
    connection._tenant_api.Get = AsyncMock()
    connection._tenant_api.List = AsyncMock(
        side_effect=[
            api.ListTenantsResponse(
                total_count=2,
                result=[api.TenantListItem(id="first", name="First")],
            ),
            api.ListTenantsResponse(
                total_count=2,
                result=[api.TenantListItem(id="second", name="Second")],
            ),
        ]
    )
    connection._application_api.List = AsyncMock(
        side_effect=[
            api.ListApplicationsResponse(
                total_count=2,
                result=[api.ApplicationListItem(id="app1", name="Sensors")],
            ),
            api.ListApplicationsResponse(
                total_count=2,
                result=[api.ApplicationListItem(id="app2", name="Relays")],
            ),
            api.ListApplicationsResponse(
                total_count=1,
                result=[api.ApplicationListItem(id="app3", name="Sensors")],
            ),
        ]
    )
    assert await connection.applications() == {
        "app1": "Sensors",
        "app2": "Relays",
        "app3": "Sensors",
    }
    requests = [
        call.args[0] for call in connection._application_api.List.call_args_list
    ]
    assert [(request.tenant_id, request.offset) for request in requests] == [
        ("first", 0),
        ("first", 1),
        ("second", 0),
    ]
    connection._tenant_api.Get.assert_not_awaited()
    await connection.close()


async def test_explicit_tenant_applications(connection: ChirpStackConnection) -> None:
    """An explicit tenant works without permission to list tenants."""
    connection._tenant_api.Get = AsyncMock()
    connection._tenant_api.List = AsyncMock(side_effect=AssertionError("Not allowed"))
    connection._application_api.List = AsyncMock(
        return_value=api.ListApplicationsResponse(
            total_count=1,
            result=[api.ApplicationListItem(id="application", name="Sensors")],
        )
    )
    assert await connection.applications() == {"application": "Sensors"}
    assert connection._tenant_api.Get.call_args.args[0].id == "tenant"
    assert connection._application_api.List.call_args.args[0].tenant_id == "tenant"
    connection._tenant_api.List.assert_not_awaited()
    await connection.close()


async def test_all_tenants_subscription(connection: ChirpStackConnection) -> None:
    """Inventory and live activity from multiple tenants share one event feed."""
    connection.tenant_id = None
    connection.application_ids = ["app1", "app2"]
    connection._tenant_api.List = AsyncMock(
        return_value=api.ListTenantsResponse(
            total_count=2,
            result=[api.TenantListItem(id="first"), api.TenantListItem(id="second")],
        )
    )

    async def applications(request, **kwargs):
        return api.ListApplicationsResponse(
            total_count=1,
            result=[
                api.ApplicationListItem(
                    id="app1" if request.tenant_id == "first" else "app2"
                )
            ],
        )

    device_ids = {"app1": "0000000000000001", "app2": "0000000000000002"}

    async def devices(request, **kwargs):
        return api.ListDevicesResponse(
            total_count=1,
            result=[
                api.DeviceListItem(
                    dev_eui=device_ids[request.application_id],
                    device_profile_id="profile",
                )
            ],
        )

    connection._application_api.List = AsyncMock(side_effect=applications)
    connection._device_api.List = AsyncMock(side_effect=devices)
    connection._profile_api.Get = AsyncMock(return_value=api.GetDeviceProfileResponse())
    callback = Mock()
    try:
        with patch.object(connection, "_stream", AsyncMock()) as stream:
            await connection.async_connect()
            stop = await connection.async_subscribe(vendor_ids=None, callback=callback)
            await asyncio.sleep(0)
            assert {call.args[0] for call in stream.call_args_list} == set(
                device_ids.values()
            )
            for dev_eui in device_ids.values():
                await connection.handle_activity(
                    DeviceEventData(
                        network_id="network",
                        dev_eui=dev_eui,
                        type=EventType.UPLINK,
                        received_at=datetime.now(UTC),
                        data=integration.UplinkEvent(data=PAYLOAD, f_port=1),
                    )
                )
            events = [call.args[0] for call in callback.call_args_list]
            assert [event.type for event in events] == [
                EventType.ADDED,
                EventType.ADDED,
                EventType.UPLINK,
                EventType.UPLINK,
            ]
            assert {event.descriptor.application_id for event in events[:2]} == {
                "app1",
                "app2",
            }
            stop()
    finally:
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
        ChirpStackConnection(
            endpoint, "secret", application_ids=[], network_id="network"
        )


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
        await connection.async_connect()
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
            "https://example.org:8080", "key", application_ids=[], network_id="network"
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
        await connection.async_connect()
    connection.channel.close.assert_awaited_once()


async def test_multiple_subscriptions(connection: ChirpStackConnection) -> None:
    """Removing one subscription leaves other consumers and the transport alive."""
    with patch.object(connection, "_snapshot", AsyncMock(return_value={})):
        await connection.async_connect()
        first, second = Mock(), Mock()
        unsubscribe = await connection.async_subscribe(vendor_ids=None, callback=first)
        await connection.async_subscribe(vendor_ids=None, callback=second)
        unsubscribe()
        unsubscribe()
        connection._emit(connection._inventory_event(EventType.ADDED, DESCRIPTOR))
        first.assert_not_called()
        second.assert_called_once()
        assert connection.available
    await connection.close()


async def test_vendor_filters_and_identity_changes(
    connection: ChirpStackConnection,
) -> None:
    """Route existing devices, vendor changes, activity, and removals."""
    first, second = Mock(), Mock()
    changed = replace(DESCRIPTOR, vendor_id=676)
    with (
        patch.object(
            connection,
            "_snapshot",
            AsyncMock(return_value={DESCRIPTOR.dev_eui: DESCRIPTOR}),
        ) as snapshot,
        patch.object(connection, "_stream", AsyncMock()),
    ):
        await connection.async_connect()
        await connection.async_subscribe(vendor_ids=frozenset({744}), callback=first)
        await connection.async_subscribe(vendor_ids=frozenset({676}), callback=second)
        first.assert_called_once()
        second.assert_not_called()
        snapshot.return_value = {changed.dev_eui: changed}
        await connection.refresh()
        assert first.call_args.args[0].type == EventType.REMOVED
        assert second.call_args.args[0].type == EventType.UPDATED
        await connection.handle_activity(
            DeviceEventData(
                network_id="network",
                dev_eui=changed.dev_eui,
                type=EventType.UPLINK,
                received_at=datetime.now(UTC),
            )
        )
        assert first.call_count == 2
        assert second.call_args.args[0].type == EventType.UPLINK
        snapshot.return_value = {}
        await connection.refresh()
        assert second.call_args.args[0].type == EventType.REMOVED
    await connection.close()


@pytest.mark.parametrize("failure", [False, True])
async def test_disconnect_listeners_and_stale_connections(
    connection: ChirpStackConnection, failure: bool
) -> None:
    removed, active = Mock(), Mock()
    unsubscribe = connection.on_disconnect(removed)
    connection.on_disconnect(active)
    unsubscribe()
    unsubscribe()
    with patch.object(connection, "_snapshot", AsyncMock(return_value={})):
        await connection.async_connect()
    if failure:
        connection._failed(ConnectionUnavailable())
    await connection.close()
    assert not connection.available
    removed.assert_not_called()
    active.assert_called_once_with()
    with pytest.raises(ConnectionUnavailable):
        await connection.async_subscribe(vendor_ids=frozenset({744}), callback=Mock())
    with pytest.raises(ConnectionUnavailable):
        connection.on_disconnect(Mock())


async def test_bad_listener_does_not_block_other_consumers(
    connection: ChirpStackConnection,
) -> None:
    good = Mock()
    connection.on_disconnect(Mock(side_effect=ValueError))
    connection.on_disconnect(good)
    with patch.object(connection, "_snapshot", AsyncMock(return_value={})):
        await connection.async_connect()
    await connection.async_subscribe(
        vendor_ids=None, callback=Mock(side_effect=ValueError)
    )
    events = Mock()
    await connection.async_subscribe(vendor_ids=None, callback=events)
    connection._emit(connection._inventory_event(EventType.ADDED, DESCRIPTOR))
    events.assert_called_once()
    await connection.close()
    good.assert_called_once()


async def test_identical_subscriptions_are_independent(
    connection: ChirpStackConnection,
) -> None:
    callback = Mock()
    with patch.object(connection, "_snapshot", AsyncMock(return_value={})):
        await connection.async_connect()
    first = await connection.async_subscribe(vendor_ids=None, callback=callback)
    second = await connection.async_subscribe(vendor_ids=None, callback=callback)
    second()
    second()
    connection._emit(connection._inventory_event(EventType.ADDED, DESCRIPTOR))
    callback.assert_called_once()
    first()
    await connection.close()


async def test_disconnect_owner_can_close_before_consumers_are_notified(
    connection: ChirpStackConnection,
) -> None:
    """An eager owner reload must not swallow later disconnect notifications."""
    tasks = []

    def close_transport():
        tasks.append(
            asyncio.Task(
                connection.close(), loop=asyncio.get_running_loop(), eager_start=True
            )
        )

    consumer = Mock()
    connection.on_disconnect(close_transport)
    connection.on_disconnect(consumer)
    with patch.object(connection, "_snapshot", AsyncMock(return_value={})):
        await connection.async_connect()
    connection._failed(ConnectionUnavailable())
    await asyncio.gather(*tasks)
    consumer.assert_called_once_with()
