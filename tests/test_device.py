"""Device notifications and declarative collection registration."""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from lorawan_connection import Device, DeviceCollection, DeviceDescriptor, DeviceEvent

from .conftest import DESCRIPTOR, inventory


class Sensor(Device):
    vendor_id = 744
    catalog_model_id = "model"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        super().__init__(descriptor)
        self.temperature = 0
        self.channels: dict[int, float] = {}

    def handle_event(self, event: DeviceEvent) -> None:
        pass


class OtherSensor(Sensor):
    catalog_model_id = "other"


class Sensors(DeviceCollection[Sensor]):
    DEVICES = (Sensor,)


def test_state_and_notifications_are_separate() -> None:
    device = Sensor(DESCRIPTOR)
    seen: list[tuple[int, dict[int, float]]] = []
    device.add_update_listener(
        lambda: seen.append((device.temperature, device.channels.copy()))
    )
    assert seen == []
    device.temperature = 2
    device.channels[1] = 31.4
    assert seen == []
    device.notify()
    device.notify()
    assert seen == [(2, {1: 31.4}), (2, {1: 31.4})]


def test_independent_duplicate_subscriptions() -> None:
    device = Sensor(DESCRIPTOR)
    listener = Mock()
    stop = device.add_update_listener(listener)
    device.add_update_listener(listener)
    stop()
    stop()
    device.notify()
    listener.assert_called_once_with()


def test_callback_can_unsubscribe_and_add_listeners() -> None:
    device = Sensor(DESCRIPTOR)
    later = Mock()
    removed = Mock()

    def first() -> None:
        stop_removed()
        device.add_update_listener(later)

    device.add_update_listener(first)
    stop_removed = device.add_update_listener(removed)
    device.notify()
    later.assert_not_called()
    removed.assert_not_called()
    device.notify()
    later.assert_called_once_with()


def test_listener_error_is_isolated(caplog: pytest.LogCaptureFixture) -> None:
    device = Sensor(DESCRIPTOR)
    device.add_update_listener(Mock(side_effect=ValueError("bad consumer")))
    listener = Mock()
    device.add_update_listener(listener)
    device.notify()
    listener.assert_called_once_with()
    assert "bad consumer" in caplog.text


def test_close_during_notification_stops_remaining_listeners() -> None:
    device = Sensor(DESCRIPTOR)
    device.add_update_listener(device.close)
    listener = Mock()
    stop = device.add_update_listener(listener)
    device.notify()
    device.close()
    device.notify()
    stop()
    listener.assert_not_called()
    assert device.closed
    with pytest.raises(RuntimeError, match="closed"):
        device.add_update_listener(listener)


def test_declarative_collection_and_inheritance() -> None:
    class Inherited(Sensors):
        pass

    for collection_class in (Sensors, Inherited):
        collection = collection_class(Mock(network_id="network"))
        collection.handle_event(inventory())
        assert isinstance(collection.devices[DESCRIPTOR.dev_eui], Sensor)
        collection.close()


def test_subclass_registry_override_does_not_modify_parent() -> None:
    class Others(Sensors):
        DEVICES = (OtherSensor,)

    parent = Sensors(Mock(network_id="network"))
    child = Others(Mock(network_id="network"))
    for descriptor in (
        DESCRIPTOR,
        replace(DESCRIPTOR, dev_eui="0000000000000002", catalog_model_id="other"),
    ):
        parent.handle_event(inventory(descriptor))
        child.handle_event(inventory(descriptor))
    assert list(parent.devices) == [DESCRIPTOR.dev_eui]
    assert list(child.devices) == ["0000000000000002"]


def test_explicit_registry_overrides_declaration_and_is_copied() -> None:
    models = [OtherSensor]
    collection = Sensors(Mock(network_id="network"), models)
    models.clear()
    collection.handle_event(inventory())
    assert not collection.devices
    collection.handle_event(inventory(replace(DESCRIPTOR, catalog_model_id="other")))
    assert isinstance(collection.devices[DESCRIPTOR.dev_eui], OtherSensor)
    empty = Sensors(Mock(network_id="network"), [])
    empty.handle_event(inventory())
    assert not empty.devices


def test_duplicate_identity_rejected_before_any_events() -> None:
    class Duplicate(Sensor):
        pass

    with pytest.raises(ValueError, match="Duplicate catalog identity"):
        DeviceCollection(Mock(network_id="network"), [Sensor, Duplicate])

    class Duplicates(DeviceCollection[Sensor]):
        DEVICES = (Sensor, Duplicate)

    with pytest.raises(ValueError, match="Duplicate catalog identity"):
        Duplicates(Mock(network_id="network"))


def test_missing_vendor_and_model_are_unsupported() -> None:
    collection = Sensors(Mock(network_id="network"))
    collection.handle_event(inventory(replace(DESCRIPTOR, vendor_id=None)))
    collection.handle_event(inventory(replace(DESCRIPTOR, catalog_model_id="unknown")))
    assert not collection.devices


def test_connection_is_required() -> None:
    with pytest.raises(TypeError, match="connection"):
        Sensors()


async def test_collections_share_connection_without_owning_it() -> None:
    connection = Mock(
        network_id="network", async_send_downlink=AsyncMock(return_value="queue-id")
    )
    first = Sensors(connection)
    second = Sensors(connection)
    first.handle_event(inventory())
    second.handle_event(inventory())
    first.close()

    device = second.devices[DESCRIPTOR.dev_eui]
    await device.async_send_downlink(data=b"command", f_port=2, wait_for_ack=False)
    sent = connection.async_send_downlink.call_args.args[0]
    assert sent.dev_eui == DESCRIPTOR.dev_eui
    assert sent.data == b"command"
    assert not device.closed
    assert second.network_id == connection.network_id
    second.close()
    connection.close.assert_not_called()
    connection.async_subscribe.assert_not_called()
