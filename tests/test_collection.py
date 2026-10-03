"""Behavior of collections, identity changes, replay, and teardown."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from lorawan_connection import (
    DeviceCollection,
    DeviceDescriptor,
    DeviceEventData,
    EventType,
)

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
    collection = Collection("network")
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
    collection = Collection("network")
    collection.handle_event(inventory())
    other = replace(DESCRIPTOR, dev_eui="0000000000000002")
    collection.handle_event(inventory(other))
    event = DeviceEventData("network", "02:01:01:01:01:01:01:01", kind, NOW)
    collection.handle_event(event)
    assert collection.devices[DESCRIPTOR.dev_eui].events[-1] is event
    assert len(collection.devices[other.dev_eui].events) == 1


def test_unknown_activity_never_creates_a_device() -> None:
    collection = Collection("network")
    collection.handle_event(
        DeviceEventData("network", DESCRIPTOR.dev_eui, EventType.UPLINK, NOW)
    )
    assert not collection.devices


@pytest.mark.parametrize(
    "event",
    [
        replace(inventory(), network_id="other"),
        replace(inventory(), descriptor=None),
        replace(inventory(), descriptor=replace(DESCRIPTOR, network_id="other")),
        replace(inventory(), dev_eui="0000000000000002"),
        inventory(replace(DESCRIPTOR, vendor_id=42)),
        inventory(replace(DESCRIPTOR, catalog_model_id="unsupported")),
    ],
)
def test_unusable_identity_is_ignored(event: DeviceEventData) -> None:
    collection = Collection("network")
    collection.handle_event(event)
    assert not collection.devices


def test_identity_change_retires_before_replacement() -> None:
    collection = Collection("network")
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
    collection = Collection("network")
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
    collection = Collection("network")
    collection.subscribe_device_added(Mock(side_effect=ValueError("consumer error")))
    good = Mock()
    collection.subscribe_device_added(good)
    collection.handle_event(inventory())
    good.assert_called_once()
    assert "consumer error" in caplog.text


def test_close_failure_does_not_leak_other_models(
    caplog: pytest.LogCaptureFixture,
) -> None:
    collection = Collection("network")
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
    collection = Collection("network")
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
    collection = Collection("network")
    collection.handle_event(inventory())
    collection.handle_event(inventory(replace(DESCRIPTOR, dev_eui="0000000000000002")))
    seen: list[DeviceModel] = []

    def added(device: DeviceModel) -> None:
        seen.append(device)
        collection.close()

    collection.subscribe_device_added(added)
    assert len(seen) == 1


def test_subclass_must_implement_factory() -> None:
    with pytest.raises(NotImplementedError):
        DeviceCollection[DeviceModel]("network").handle_event(inventory())


def test_removed_callback_can_close_during_model_replacement() -> None:
    collection = Collection("network")
    collection.handle_event(inventory())
    original = collection.devices[DESCRIPTOR.dev_eui]
    collection.subscribe_device_removed(lambda device: collection.close())
    collection.handle_event(
        inventory(replace(DESCRIPTOR, catalog_model_id="other"), EventType.UPDATED)
    )
    assert original.close_count == 1
    assert not collection.devices
