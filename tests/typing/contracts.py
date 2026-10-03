"""Static conformance of every supplied fixture to its read-only Protocol."""

from datetime import UTC, datetime

from lorawan_connection import (
    Ack,
    AckData,
    Coordinates,
    CoordinatesData,
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
