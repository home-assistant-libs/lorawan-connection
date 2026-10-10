"""The in-memory connection exercises the same collection lifecycle as a server."""

import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest

from lorawan_connection import (
    AckEvent,
    ConnectionUnavailable,
    DeviceCollection,
    Downlink,
    DownlinkError,
    EventType,
    UplinkEvent,
)
from lorawan_connection.mock import MockConnection

from .conftest import DESCRIPTOR, NOW, DeviceModel, inventory


def activity() -> UplinkEvent:
    return UplinkEvent(received_at=NOW, descriptor=DESCRIPTOR, data=b"reading")


async def test_replay_live_updates_and_unsubscribe() -> None:
    connection = MockConnection([DESCRIPTOR])
    devices = DeviceCollection(connection, [DeviceModel])
    await devices.async_setup()
    device = devices.devices[DESCRIPTOR.dev_eui]
    assert device.events[0].type == EventType.ADDED
    uplink = activity()
    connection.emit(uplink)
    assert device.events[-1] is uplink
    renamed = replace(DESCRIPTOR, name="Renamed")
    connection.emit(inventory(renamed, EventType.UPDATED))
    assert device.descriptor is renamed
    devices.close()
    count = len(device.events)
    connection.emit(uplink)
    assert len(device.events) == count
    replacement = DeviceCollection(connection, [DeviceModel])
    await replacement.async_setup()
    assert replacement.devices[DESCRIPTOR.dev_eui].descriptor is renamed
    replacement.close()


async def test_vendor_filters_and_changes() -> None:
    connection = MockConnection([DESCRIPTOR])
    original, other = Mock(), Mock()
    await connection.async_subscribe(
        brands=frozenset({("chirpstack", 744)}), listener=original
    )
    await connection.async_subscribe(
        brands=frozenset({("chirpstack", 42)}), listener=other
    )
    original.assert_called_once()
    other.assert_not_called()
    changed = inventory(replace(DESCRIPTOR, brand_id=42), EventType.UPDATED)
    connection.emit(changed)
    assert original.call_args.args[0].type == EventType.REMOVED
    assert original.call_args.args[0].descriptor is DESCRIPTOR
    other.assert_called_once_with(changed)
    connection.emit(activity())
    assert original.call_count == 2
    assert other.call_count == 2
    connection.emit(inventory(kind=EventType.REMOVED))
    assert other.call_args.args[0].type == EventType.REMOVED
    assert not connection.devices
    late = Mock()
    await connection.async_subscribe(
        brands=frozenset({("chirpstack", 42)}), listener=late
    )
    late.assert_not_called()


async def test_duplicate_callbacks_unsubscribe_independently() -> None:
    connection = MockConnection([DESCRIPTOR])
    listener = Mock()
    unsubscribe = await connection.async_subscribe(
        brands=frozenset({("chirpstack", 744)}), listener=listener
    )
    await connection.async_subscribe(
        brands=frozenset({("chirpstack", 744)}), listener=listener
    )
    listener.reset_mock()
    unsubscribe()
    unsubscribe()
    connection.emit(activity())
    listener.assert_called_once()


async def test_callback_failure_and_unsubscribe_during_delivery() -> None:
    connection = MockConnection([DESCRIPTOR])
    await connection.async_subscribe(
        brands=frozenset({("chirpstack", 744)}),
        listener=Mock(side_effect=ValueError("bad listener")),
    )

    def first(event):
        if event.type == EventType.UPLINK:
            unsubscribe()

    await connection.async_subscribe(
        brands=frozenset({("chirpstack", 744)}), listener=first
    )
    listener = Mock()
    unsubscribe = await connection.async_subscribe(
        brands=frozenset({("chirpstack", 744)}), listener=listener
    )
    listener.reset_mock()
    connection.emit(activity())
    listener.assert_not_called()


async def test_commands_wait_for_explicit_ack() -> None:
    connection = MockConnection([DESCRIPTOR])
    devices = DeviceCollection(connection, [DeviceModel])
    await devices.async_setup()
    device = devices.devices[DESCRIPTOR.dev_eui]
    pending = asyncio.create_task(device.async_send_downlink(data=b"command", f_port=2))
    await asyncio.sleep(0)
    assert not pending.done()
    queue_id, downlink = next(iter(connection.downlinks.items()))
    assert downlink == Downlink(DESCRIPTOR.dev_eui, 2, b"command", confirmed=True)
    connection.emit(
        AckEvent(
            received_at=NOW,
            descriptor=DESCRIPTOR,
            queue_item_id=queue_id,
            acknowledged=True,
        )
    )
    assert await pending is None
    second = await connection.async_send_downlink(downlink)
    assert second != queue_id
    assert connection.downlinks[second] is downlink
    devices.close()


async def test_disconnect_ends_subscriptions_and_pending_commands() -> None:
    connection = MockConnection([DESCRIPTOR])
    devices = DeviceCollection(connection, [DeviceModel])
    await devices.async_setup()
    connection.on_disconnect(devices.close)
    discarded = Mock()
    unsubscribe = connection.on_disconnect(discarded)
    unsubscribe()
    unsubscribe()
    remaining = Mock()
    connection.on_disconnect(Mock(side_effect=ValueError("bad observer")))
    connection.on_disconnect(listener=remaining)
    pending = asyncio.create_task(
        devices.devices[DESCRIPTOR.dev_eui].async_send_downlink(
            data=b"command", f_port=2
        )
    )
    await asyncio.sleep(0)
    connection.disconnect()
    connection.disconnect()
    remaining.assert_called_once_with()
    discarded.assert_not_called()
    assert not devices.devices
    with pytest.raises(DownlinkError, match="Connection was lost"):
        await pending
    with pytest.raises(ConnectionUnavailable):
        await connection.async_subscribe(
            brands=frozenset({("chirpstack", 744)}), listener=Mock()
        )
    with pytest.raises(ConnectionUnavailable):
        connection.emit(activity())
    with pytest.raises(ConnectionUnavailable):
        connection.on_disconnect(Mock())
    with pytest.raises(DownlinkError, match="disconnected"):
        await connection.async_send_downlink(
            Downlink(DESCRIPTOR.dev_eui, 2, b"command")
        )


async def test_unknown_device_and_mixed_networks_are_rejected() -> None:
    connection = MockConnection()
    with pytest.raises(ValueError, match="Add the device"):
        connection.emit(activity())
    with pytest.raises(DownlinkError, match="Unknown device"):
        await connection.async_send_downlink(
            Downlink(DESCRIPTOR.dev_eui, 2, b"command")
        )
    connection.emit(inventory())
    with pytest.raises(ValueError, match="another network"):
        connection.emit(inventory(replace(DESCRIPTOR, network_id="other")))
