"""Command waits correlate delivery results without guessing device state."""

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from chirpstack_api import integration

from lorawan_connection import (
    AckData,
    DeviceCollection,
    DeviceEventData,
    DownlinkError,
    EventType,
    TxAckData,
)

from .conftest import DESCRIPTOR, NOW, DeviceModel, inventory


@pytest.fixture
def sender() -> AsyncMock:
    return AsyncMock(return_value="queue-id")


@pytest.fixture
def devices(sender: AsyncMock) -> Iterator[DeviceCollection[DeviceModel]]:
    collection = DeviceCollection(
        [DeviceModel], network_id="network", send_downlink=sender
    )
    collection.handle_event(inventory())
    yield collection
    collection.close()


def ack(
    devices: DeviceCollection[DeviceModel],
    queue_id: str = "queue-id",
    acknowledged: bool = True,
    dev_eui: str = DESCRIPTOR.dev_eui,
) -> None:
    devices.handle_event(
        DeviceEventData(
            "network",
            dev_eui,
            EventType.ACK,
            NOW,
            data=AckData(queue_id, acknowledged),
        )
    )


async def test_waits_for_matching_device_ack(devices, sender) -> None:
    device = devices.devices[DESCRIPTOR.dev_eui]
    command = asyncio.create_task(device.async_send_downlink(data=b"command", f_port=2))
    await asyncio.sleep(0)
    assert sender.call_args.args[0].confirmed
    assert not command.done()
    ack(devices, "another-command")
    ack(devices, dev_eui="0000000000000002")
    devices.handle_event(
        DeviceEventData(
            "network",
            DESCRIPTOR.dev_eui,
            EventType.TX_ACK,
            NOW,
            data=TxAckData("gateway", 1),
        )
    )
    await asyncio.sleep(0)
    assert not command.done()
    ack(devices)
    assert await command == "queue-id"
    assert device.events[-1].type == EventType.ACK


@pytest.mark.parametrize("acknowledged", [True, False])
async def test_ack_before_enqueue_returns(devices, sender, acknowledged) -> None:
    async def send(downlink):
        devices.handle_event(
            DeviceEventData(
                "network",
                DESCRIPTOR.dev_eui,
                EventType.ACK,
                NOW,
                data=integration.AckEvent(
                    queue_item_id="queue-id", acknowledged=acknowledged
                ),
            )
        )
        return "queue-id"

    sender.side_effect = send
    device = devices.devices[DESCRIPTOR.dev_eui]
    if acknowledged:
        assert await device.async_send_downlink(data=b"command", f_port=2) == "queue-id"
    else:
        with pytest.raises(DownlinkError, match="did not acknowledge"):
            await device.async_send_downlink(data=b"command", f_port=2)
    assert not device._pending_acks
    assert not device._early_acks


async def test_negative_ack(devices) -> None:
    device = devices.devices[DESCRIPTOR.dev_eui]
    command = asyncio.create_task(device.async_send_downlink(data=b"command", f_port=2))
    await asyncio.sleep(0)
    ack(devices, acknowledged=False)
    with pytest.raises(DownlinkError, match="did not acknowledge"):
        await command


async def test_concurrent_commands(devices, sender) -> None:
    sender.side_effect = ["first", "second"]
    device = devices.devices[DESCRIPTOR.dev_eui]
    first = asyncio.create_task(device.async_send_downlink(data=b"one", f_port=2))
    second = asyncio.create_task(device.async_send_downlink(data=b"two", f_port=2))
    await asyncio.sleep(0)
    ack(devices, "second")
    assert await second == "second"
    assert not first.done()
    ack(devices, "first")
    assert await first == "first"


async def test_opt_out_is_unconfirmed(devices, sender) -> None:
    device = devices.devices[DESCRIPTOR.dev_eui]
    assert (
        await device.async_send_downlink(data=b"command", f_port=2, wait_for_ack=False)
        == "queue-id"
    )
    assert not sender.call_args.args[0].confirmed
    assert not device._pending_acks


async def test_caller_timeout_cleans_up(devices, sender) -> None:
    device = devices.devices[DESCRIPTOR.dev_eui]
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0):
            await device.async_send_downlink(data=b"command", f_port=2)
    assert not device._pending_acks
    assert not device._early_acks
    ack(devices)
    assert not device._early_acks
    sender.assert_awaited_once()


@pytest.mark.parametrize("action", ["close", "remove", "replace"])
async def test_retired_device_stops_waiting(devices, action) -> None:
    device = devices.devices[DESCRIPTOR.dev_eui]
    command = asyncio.create_task(device.async_send_downlink(data=b"command", f_port=2))
    await asyncio.sleep(0)
    if action == "close":
        devices.close()
    elif action == "remove":
        devices.handle_event(inventory(kind=EventType.REMOVED))
    else:
        devices.handle_event(
            inventory(
                replace(DESCRIPTOR, catalog_model_id="new-model"), EventType.UPDATED
            )
        )
    with pytest.raises(DownlinkError, match="closed"):
        await command
    assert not device._pending_acks


async def test_closed_while_enqueuing(devices, sender) -> None:
    async def send(downlink):
        devices.close()
        return "queue-id"

    sender.side_effect = send
    device = devices.devices[DESCRIPTOR.dev_eui]
    with pytest.raises(DownlinkError, match="closed"):
        await device.async_send_downlink(data=b"command", f_port=2)
    assert not device._pending_acks


@pytest.mark.parametrize("error", [DownlinkError("denied"), asyncio.CancelledError()])
async def test_enqueue_failure_cleans_up(devices, sender, error) -> None:
    async def send(downlink):
        ack(devices)
        raise error

    sender.side_effect = send
    device = devices.devices[DESCRIPTOR.dev_eui]
    with pytest.raises(type(error)):
        await device.async_send_downlink(data=b"command", f_port=2)
    assert not device._pending_acks
    assert not device._early_acks
    assert device._enqueuing == 0
    sender.assert_awaited_once()
