"""Static conformance of every supplied fixture to its read-only Protocol."""

from datetime import UTC, datetime

from lorawan_connection import (
    Ack,
    AckData,
    Connection,
    Coordinates,
    CoordinatesData,
    DeviceCollection,
    DeviceEvent,
    DeviceEventData,
    EventType,
    Join,
    JoinData,
    Location,
    LocationData,
    Log,
    LogData,
    Status,
    StatusData,
    TxAck,
    TxAckData,
    Uplink,
    UplinkData,
)
from lorawan_connection.chirpstack import ChirpStackConnection
from sensecap_lorawan import S2101, SenseCapDeviceCollection

uplink: Uplink = UplinkData(b"\x00")
join: Join = JoinData("01234567")
status: Status = StatusData()
ack: Ack = AckData("queue", True)
tx_ack: TxAck = TxAckData("gateway", 1)
log: Log = LogData("message", 1, 1)
coordinates: Coordinates = CoordinatesData(1, 2, 3)
location: Location = LocationData(coordinates)
event: DeviceEvent = DeviceEventData(
    "network", "0000000000000001", EventType.UPLINK, datetime.now(UTC), data=uplink
)


# The class registry preserves the concrete model type for callers.

connection: Connection = ChirpStackConnection(
    "http://localhost:8080", "key", application_ids=[], network_id="network"
)
model_class: type[S2101] = S2101
collection: DeviceCollection[S2101] = DeviceCollection(connection, [S2101])
declared: DeviceCollection[S2101] = SenseCapDeviceCollection(connection)


def observe(model: S2101) -> None:
    model.add_update_listener(lambda: print(model.temperature))
    model.close()
