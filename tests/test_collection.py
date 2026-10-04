"""Behavior of collections, identity changes, replay, and teardown."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from lorawan_connection import (
    ConnectionUnavailable,
    DeviceCollection,
    DeviceDescriptor,
    DeviceEventData,
    EventType,
)
from lorawan_connection.mock import MockConnection

from .conftest import DESCRIPTOR, NOW, DeviceModel, inventory


class Collection(DeviceCollection[DeviceModel]):
    def _create_device(self, descriptor: DeviceDescriptor) -> DeviceModel | None:
        if descriptor.vendor_id == 744 and descriptor.catalog_model_id in {
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
    "kind",
    [
        kind
        for kind in EventType
        if kind not in {EventType.ADDED, EventType.UPDATED, EventType.REMOVED}
    ],
)
def test_all_activity_routes_by_identity(kind: EventType) -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    other = replace(DESCRIPTOR, dev_eui="0000000000000002")
    collection.handle_event(inventory(other))
    event = DeviceEventData(
        network_id="network",
        dev_eui="02:01:01:01:01:01:01:01",
        type=kind,
        received_at=NOW,
    )
    collection.handle_event(event)
    assert collection.devices[DESCRIPTOR.dev_eui].events[-1] is event
    assert len(collection.devices[other.dev_eui].events) == 1


def test_unknown_activity_never_creates_a_device() -> None:
    collection = Collection(MockConnection())
    collection.handle_event(
        DeviceEventData(
            network_id="network",
            dev_eui=DESCRIPTOR.dev_eui,
            type=EventType.UPLINK,
            received_at=NOW,
        )
    )
    assert not collection.devices


@pytest.mark.parametrize(
    "event",
    [
        SimpleNamespace(
            network_id="other",
            dev_eui=DESCRIPTOR.dev_eui,
            type=EventType.ADDED,
            descriptor=DESCRIPTOR,
        ),
        replace(inventory(), descriptor=None),
        SimpleNamespace(
            network_id="network",
            dev_eui="0000000000000002",
            type=EventType.ADDED,
            descriptor=DESCRIPTOR,
        ),
        inventory(replace(DESCRIPTOR, vendor_id=42)),
        inventory(replace(DESCRIPTOR, catalog_model_id="unsupported")),
    ],
)
def test_unusable_identity_is_ignored(event: DeviceEventData) -> None:
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
        inventory(replace(DESCRIPTOR, catalog_model_id="other"), EventType.UPDATED)
    )
    assert order == ["removed:1", "added"]
    assert collection.devices[DESCRIPTOR.dev_eui] is not original
    collection.handle_event(
        inventory(replace(DESCRIPTOR, vendor_id=1), EventType.UPDATED)
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


def test_empty_registry_ignores_unknown_devices() -> None:
    collection = DeviceCollection[DeviceModel](MockConnection())
    collection.handle_event(inventory())
    assert not collection.devices


def test_removed_callback_can_close_during_model_replacement() -> None:
    collection = Collection(MockConnection())
    collection.handle_event(inventory())
    original = collection.devices[DESCRIPTOR.dev_eui]
    collection.subscribe_device_removed(lambda device: collection.close())
    collection.handle_event(
        inventory(replace(DESCRIPTOR, catalog_model_id="other"), EventType.UPDATED)
    )
    assert original.close_count == 1
    assert not collection.devices


async def test_setup_subscribes_to_registered_vendors() -> None:
    class Sensor(DeviceModel):
        vendor_id = 744
        catalog_model_id = "model"

    connection = MockConnection([DESCRIPTOR])
    original_subscribe = connection.async_subscribe
    unsubscribe = Mock()
    collection = DeviceCollection(connection, [Sensor])
    added = Mock()
    collection.subscribe_device_added(added)

    async def subscribe(*, vendor_ids, callback):
        unsubscribe.side_effect = await original_subscribe(
            vendor_ids=vendor_ids, callback=callback
        )
        return unsubscribe

    connection.async_subscribe = AsyncMock(side_effect=subscribe)
    await collection.async_setup()
    connection.async_subscribe.assert_awaited_once_with(
        vendor_ids=frozenset({744}), callback=collection.handle_event
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
        inventory(replace(DESCRIPTOR, catalog_model_id="other"), EventType.UPDATED)
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
