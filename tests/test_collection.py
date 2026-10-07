"""Behavior of collections, identity changes, replay, and teardown."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from lorawan_connection import (
    AckEvent,
    ConnectionUnavailable,
    Device,
    DeviceCollection,
    DeviceDescriptor,
    DeviceEvent,
    EventType,
    JoinEvent,
    LocationEvent,
    LogEvent,
    StatusEvent,
    TxAckEvent,
    UplinkEvent,
)
from lorawan_connection.mock import MockConnection

from .conftest import DESCRIPTOR, NOW, DeviceModel, inventory


class Collection(DeviceCollection[DeviceModel]):
    def _create_device(self, descriptor: DeviceDescriptor) -> DeviceModel | None:
        if descriptor.brand_id == 744 and descriptor.model_id in {
            "model",
            "other",
        }:
            return DeviceModel(descriptor)
        return None


def test_inventory_creates_models_and_replays() -> None:
    collection = Collection(MockConnection())
    event = inventory()
    collection.handle_event(event)
    added = Mock()
    stop = collection.subscribe_device_added(added)
    model = collection.devices[DESCRIPTOR.dev_eui]
    added.assert_called_once_with(model)
    assert model.events == [event]
    renamed = replace(
        DESCRIPTOR, name="New name", profile_id="new-profile", application_id="new-app"
    )
    collection.handle_event(inventory(renamed, EventType.UPDATED))
    collection.handle_event(inventory(renamed))
    assert collection.devices[DESCRIPTOR.dev_eui] is model
    assert model.descriptor == renamed
    added.assert_called_once()
    stop()
    stop()
    collection.handle_event(inventory(replace(DESCRIPTOR, dev_eui="0000000000000002")))
    added.assert_called_once()


@pytest.mark.parametrize(
    "event",
    [
        UplinkEvent(descriptor=DESCRIPTOR, received_at=NOW, data=b"payload"),
        JoinEvent(descriptor=DESCRIPTOR, received_at=NOW, dev_addr="12345678"),
        StatusEvent(descriptor=DESCRIPTOR, received_at=NOW),
        AckEvent(
            descriptor=DESCRIPTOR,
            received_at=NOW,
            queue_item_id="queue",
            acknowledged=True,
        ),
        TxAckEvent(
            descriptor=DESCRIPTOR, received_at=NOW, gateway_id="gateway", downlink_id=1
        ),
        LogEvent(
            descriptor=DESCRIPTOR,
            received_at=NOW,
            description="message",
            level=1,
            code=2,
        ),
        LocationEvent(
            descriptor=DESCRIPTOR, received_at=NOW, latitude=52, longitude=4, altitude=3
        ),
    ],
)
def test_all_activity_routes_by_identity(event: DeviceEvent) -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    other = replace(DESCRIPTOR, dev_eui="0000000000000002")
    collection.handle_event(inventory(other))
    collection.handle_event(event)
    assert collection.devices[DESCRIPTOR.dev_eui].events[-1] is event
    assert len(collection.devices[other.dev_eui].events) == 1


def test_unknown_activity_never_creates_a_device() -> None:
    collection = Collection(MockConnection())
    collection.handle_event(
        UplinkEvent(
            network_id="network", dev_eui=DESCRIPTOR.dev_eui, received_at=NOW, data=b""
        )
    )
    assert not collection.devices


@pytest.mark.parametrize(
    "event",
    [
        inventory(replace(DESCRIPTOR, brand_id=42)),
        inventory(replace(DESCRIPTOR, model_id="unsupported")),
    ],
)
def test_unusable_identity_is_ignored(event: DeviceEvent) -> None:
    collection = Collection(MockConnection())
    collection.handle_event(event)
    assert not collection.devices


def test_identity_change_retires_before_replacement() -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    original = collection.devices[DESCRIPTOR.dev_eui]
    order: list[str] = []
    collection.subscribe_device_removed(
        lambda device: order.append(f"removed:{device.close_count}")
    )
    collection.subscribe_device_added(lambda device: order.append("added"))
    order.clear()
    collection.handle_event(
        inventory(replace(DESCRIPTOR, model_id="other"), EventType.UPDATED)
    )
    assert order == ["removed:1", "added"]
    assert collection.devices[DESCRIPTOR.dev_eui] is not original
    collection.handle_event(
        inventory(replace(DESCRIPTOR, brand_id=1), EventType.UPDATED)
    )
    assert not collection.devices
    assert order == ["removed:1", "added", "removed:1"]


def test_remove_and_close_are_idempotent() -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    first = collection.devices[DESCRIPTOR.dev_eui]
    removed = Mock()
    stop = collection.subscribe_device_removed(removed)
    collection.handle_event(inventory(kind=EventType.REMOVED))
    collection.handle_event(inventory(kind=EventType.REMOVED))
    assert first.close_count == 1
    removed.assert_called_once_with(first)
    stop()
    collection.handle_event(inventory())
    second = collection.devices[DESCRIPTOR.dev_eui]
    collection.close()
    collection.close()
    assert second.close_count == 1
    removed.assert_called_once()
    collection.handle_event(inventory())
    assert not collection.devices
    with pytest.raises(RuntimeError, match="closed"):
        collection.subscribe_device_added(Mock())
    with pytest.raises(RuntimeError, match="closed"):
        collection.subscribe_device_removed(Mock())


def test_listener_failure_does_not_block_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    collection = Collection(MockConnection())
    collection.subscribe_device_added(Mock(side_effect=ValueError("consumer error")))
    good = Mock()
    collection.subscribe_device_added(good)
    collection.handle_event(inventory())
    good.assert_called_once()
    assert "consumer error" in caplog.text


def test_close_failure_does_not_leak_other_models(
    caplog: pytest.LogCaptureFixture,
) -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    collection.handle_event(inventory(replace(DESCRIPTOR, dev_eui="0000000000000002")))
    first, second = collection.devices.values()
    first.close = Mock(side_effect=ValueError("cleanup error"))
    removed = Mock()
    collection.subscribe_device_removed(removed)
    collection.close()
    assert not collection.devices
    assert second.close_count == 1
    assert removed.call_count == 2
    assert "cleanup error" in caplog.text


def test_added_callback_can_close_collection() -> None:
    collection = Collection(MockConnection())
    seen: list[DeviceModel] = []

    def added(device: DeviceModel) -> None:
        seen.append(device)
        collection.close()

    collection.subscribe_device_added(added)
    collection.handle_event(inventory())
    assert not collection.devices
    assert seen[0].events == []
    assert seen[0].close_count == 1


def test_replay_skips_models_removed_by_a_callback() -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    collection.handle_event(inventory(replace(DESCRIPTOR, dev_eui="0000000000000002")))
    seen: list[DeviceModel] = []

    def added(device: DeviceModel) -> None:
        seen.append(device)
        collection.close()

    collection.subscribe_device_added(added)
    assert len(seen) == 1


def test_explicit_empty_registry_ignores_unknown_devices() -> None:
    collection = DeviceCollection[DeviceModel](MockConnection(), [])
    collection.handle_event(inventory())
    assert not collection.devices


def test_removed_callback_can_close_during_model_replacement() -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    original = collection.devices[DESCRIPTOR.dev_eui]
    collection.subscribe_device_removed(lambda device: collection.close())
    collection.handle_event(
        inventory(replace(DESCRIPTOR, model_id="other"), EventType.UPDATED)
    )
    assert original.close_count == 1
    assert not collection.devices


async def test_setup_subscribes_to_registered_vendors() -> None:
    class Sensor(DeviceModel):
        identifiers = {"chirpstack": (744, "model")}

    connection = MockConnection([DESCRIPTOR])
    original_subscribe = connection.async_subscribe
    unsubscribe = Mock()
    collection = DeviceCollection(connection, [Sensor])
    added = Mock()
    collection.subscribe_device_added(added)

    async def subscribe(*, brands, callback):
        unsubscribe.side_effect = await original_subscribe(
            brands=brands, callback=callback
        )
        return unsubscribe

    connection.async_subscribe = AsyncMock(side_effect=subscribe)
    await collection.async_setup()
    connection.async_subscribe.assert_awaited_once_with(
        brands=frozenset({("chirpstack", 744)}), callback=collection.handle_event
    )
    device = collection.devices[DESCRIPTOR.dev_eui]
    added.assert_called_once_with(device)
    with pytest.raises(RuntimeError, match="already set up"):
        await collection.async_setup()
    collection.close()
    collection.close()
    unsubscribe.assert_called_once()
    assert device.closed
    connection.emit(inventory())
    assert not collection.devices


@pytest.mark.parametrize("error", [ConnectionUnavailable(), asyncio.CancelledError()])
async def test_setup_failure_closes_created_models(error: BaseException) -> None:
    connection = MockConnection()
    collection = Collection(connection)
    device = Mock()
    collection.subscribe_device_added(device)

    async def subscribe(**kwargs):
        kwargs["callback"](inventory())
        raise error

    connection.async_subscribe = AsyncMock(side_effect=subscribe)
    with pytest.raises(type(error)):
        await collection.async_setup()
    assert device.call_args.args[0].closed
    assert not collection.devices
    connection.emit(inventory())


async def test_close_during_setup_releases_subscription() -> None:
    unsubscribe = Mock()
    connection = MockConnection([DESCRIPTOR])
    original_subscribe = connection.async_subscribe
    collection = Collection(connection, [DeviceModel])
    collection.subscribe_device_added(lambda _: collection.close())

    async def subscribe(**kwargs):
        unsubscribe.side_effect = await original_subscribe(**kwargs)
        return unsubscribe

    connection.async_subscribe = AsyncMock(side_effect=subscribe)
    with pytest.raises(RuntimeError, match="closed during setup"):
        await collection.async_setup()
    unsubscribe.assert_called_once()
    assert not collection.devices


@pytest.mark.parametrize("replacement", [False, True])
def test_device_remove_listener(replacement: bool) -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    device = collection.devices[DESCRIPTOR.dev_eui]
    removed = Mock()

    def on_remove() -> None:
        assert device.closed
        assert DESCRIPTOR.dev_eui not in collection.devices
        removed()

    device.add_remove_listener(on_remove)
    event = (
        inventory(replace(DESCRIPTOR, model_id="other"), EventType.UPDATED)
        if replacement
        else inventory(kind=EventType.REMOVED)
    )
    collection.handle_event(event)
    collection.handle_event(event)
    removed.assert_called_once_with()
    collection.close()
    removed.assert_called_once_with()


def test_device_remove_listeners_unsubscribe_and_failures() -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    device = collection.devices[DESCRIPTOR.dev_eui]
    removed, remaining = Mock(), Mock()
    unsubscribe = device.add_remove_listener(removed)
    unsubscribe()
    unsubscribe()
    device.add_remove_listener(Mock(side_effect=ValueError("bad observer")))
    device.add_remove_listener(remaining)
    collection.handle_event(inventory(kind=EventType.REMOVED))
    removed.assert_not_called()
    remaining.assert_called_once_with()
    with pytest.raises(RuntimeError, match="closed"):
        device.add_remove_listener(Mock())


def test_collection_shutdown_does_not_report_device_removal() -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    device = collection.devices[DESCRIPTOR.dev_eui]
    removed = Mock()
    device.add_remove_listener(removed)
    collection.close()
    assert device.closed
    removed.assert_not_called()
    assert not device._remove_listeners


def test_device_remove_callback_can_close_collection() -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    device = collection.devices[DESCRIPTOR.dev_eui]
    device.add_remove_listener(collection.close)
    other = Mock()
    device.add_remove_listener(other)
    collection.handle_event(inventory(kind=EventType.REMOVED))
    other.assert_called_once_with()
    assert not collection.devices


async def test_generic_collection_includes_all_devices_and_stores_status() -> None:
    unknown = replace(
        DESCRIPTOR, brand_id=None, model_id="", dev_eui="0000000000000002"
    )
    other_brand = replace(DESCRIPTOR, brand_id=123, dev_eui="0000000000000003")
    connection = MockConnection([DESCRIPTOR, unknown, other_brand])
    collection = DeviceCollection(connection)
    added = []
    collection.subscribe_device_added(added.append)
    await collection.async_setup()
    assert len(added) == 3
    assert type(collection.devices[DESCRIPTOR.dev_eui]) is Device
    generic = collection.devices[unknown.dev_eui]
    assert type(generic) is Device
    assert type(collection.devices[other_brand.dev_eui]) is Device
    seen = []
    generic.add_update_listener(lambda: seen.append(generic.battery_level))
    status = StatusEvent(
        descriptor=unknown,
        received_at=NOW,
        battery_level=70,
        battery_level_unavailable=False,
    )
    connection.emit(status)
    assert seen == [70]
    assert generic.latest_status is status
    renamed = replace(unknown, name="Unrecognized sensor")
    connection.emit(inventory(renamed, EventType.UPDATED))
    assert collection.devices[unknown.dev_eui] is generic
    assert generic.descriptor.name == "Unrecognized sensor"
    assert generic.latest_status is status

    # Catalog identities do not select vendor models in a generic collection.
    recognized = replace(renamed, brand_id=744, model_id="model")
    connection.emit(inventory(recognized, EventType.UPDATED))
    assert generic.closed
    recognized_device = collection.devices[unknown.dev_eui]
    assert type(recognized_device) is Device
    connection.emit(inventory(renamed, EventType.UPDATED))
    assert recognized_device.closed
    assert type(collection.devices[unknown.dev_eui]) is Device
    connection.emit(inventory(renamed, EventType.REMOVED))
    assert unknown.dev_eui not in collection.devices
    collection.close()
    connection.emit(inventory(unknown))
    assert not collection.devices


async def test_generic_and_vendor_collections_share_connection() -> None:
    unknown = replace(DESCRIPTOR, brand_id=None, dev_eui="0000000000000002")
    connection = MockConnection([DESCRIPTOR, unknown])
    overview = DeviceCollection(connection)
    vendor = DeviceCollection(connection, [DeviceModel])
    await overview.async_setup()
    await vendor.async_setup()
    assert set(overview.devices) == {DESCRIPTOR.dev_eui, unknown.dev_eui}
    assert set(vendor.devices) == {DESCRIPTOR.dev_eui}
    assert all(type(device) is Device for device in overview.devices.values())
    overview.close()
    event = StatusEvent(descriptor=DESCRIPTOR, received_at=NOW)
    connection.emit(event)
    assert vendor.devices[DESCRIPTOR.dev_eui].latest_status is event
    vendor.close()
