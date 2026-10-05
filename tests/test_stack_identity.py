"""Match native identities without translating one stack's catalog to another."""

from dataclasses import replace

import pytest

from lorawan_connection import DeviceCollection, EventType
from lorawan_connection.mock import MockConnection
from sensecap_lorawan import S2101, SenseCapDeviceCollection

from .conftest import DeviceModel, inventory
from .test_sensecap_example import DESCRIPTOR


@pytest.mark.parametrize("stack", ["chirpstack", "tts"])
async def test_same_model_on_either_stack(stack: str) -> None:
    brand, model = S2101.identifiers[stack]
    descriptor = replace(DESCRIPTOR, stack=stack, brand_id=brand, model_id=model)
    connection = MockConnection([descriptor])
    collection = SenseCapDeviceCollection(connection)
    await collection.async_setup()
    assert type(collection.devices[descriptor.dev_eui]) is S2101
    collection.close()


async def test_namespace_is_part_of_subscription_and_model_matching() -> None:
    class OtherStack(DeviceModel):
        identifiers = {"other": (744, S2101.identifiers["chirpstack"][1])}

    first = DESCRIPTOR
    second = replace(DESCRIPTOR, stack="other", dev_eui="0000000000000002")
    connection = MockConnection([first, second])
    sensecap = SenseCapDeviceCollection(connection)
    other = DeviceCollection(connection, [OtherStack])
    await sensecap.async_setup()
    await other.async_setup()
    assert list(sensecap.devices) == [first.dev_eui]
    assert list(other.devices) == [second.dev_eui]
    connection.emit(inventory(replace(first, stack="other"), EventType.UPDATED))
    assert not sensecap.devices
    assert set(other.devices) == {first.dev_eui, second.dev_eui}
    sensecap.close()
    other.close()


def test_duplicate_rejected_within_one_stack() -> None:
    class Alias(DeviceModel):
        identifiers = {"tts": S2101.identifiers["tts"]}

    with pytest.raises(ValueError, match="Duplicate model identity"):
        DeviceCollection(MockConnection(), [S2101, Alias])
