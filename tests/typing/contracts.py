"""Typed event narrowing and collection model types."""

from typing import assert_type

from lorawan_connection import (
    Connection,
    Device,
    DeviceCollection,
    DeviceEvent,
    EventType,
    StatusEvent,
    UplinkEvent,
)
from lorawan_connection.backend.chirpstack import ChirpStackConnection
from lorawan_connection.mock import MockConnection
from sensecap_lorawan import S2101, SenseCapDeviceCollection


def narrow(event: DeviceEvent) -> None:
    if event.type == EventType.UPLINK:
        assert_type(event, UplinkEvent)
        assert_type(event.data, bytes)
        assert_type(event.f_port, int)
    if event.type == EventType.ACK:
        assert_type(event.queue_item_id, str)
        assert_type(event.acknowledged, bool)


connection: Connection = ChirpStackConnection(
    "http://localhost:8080", "key", application_ids=[], network_id="network"
)
mock_connection: Connection = MockConnection()
model_class: type[S2101] = S2101
collection: DeviceCollection[S2101] = DeviceCollection(connection, [S2101])
declared: DeviceCollection[S2101] = SenseCapDeviceCollection(connection)


def observe(model: S2101) -> None:
    model.add_update_listener(lambda: print(model.temperature))
    model.close()


overview = DeviceCollection(connection)
assert_type(overview, DeviceCollection[Device])
assert_type(overview.devices["0201010101010101"].latest_status, StatusEvent | None)
assert_type(overview.devices["0201010101010101"].battery_level, float | None)

assert_type(SenseCapDeviceCollection(connection).devices["0201010101010101"], S2101)
