"""Read-only structural payload contracts, with no protobuf dependency.

EventType is the discriminator. Protocols are deliberately not runtime-checkable:
several payloads share fields. A backend owns values and must not mutate them
while consumers hold references. Proto3 scalar defaults remain scalar defaults;
for example battery_level_unavailable determines whether a battery value exists.
"""

from dataclasses import dataclass
from typing import Protocol


class Uplink(Protocol):
    """Raw application payload, independent of backend codecs."""

    @property
    def data(self) -> bytes: ...

    @property
    def f_port(self) -> int: ...


class Join(Protocol):
    """Device joined the network."""

    @property
    def dev_addr(self) -> str: ...


class Status(Protocol):
    """LoRaWAN status with explicit battery presence semantics."""

    @property
    def margin(self) -> int: ...

    @property
    def external_power_source(self) -> bool: ...

    @property
    def battery_level_unavailable(self) -> bool: ...

    @property
    def battery_level(self) -> float: ...


class Ack(Protocol):
    """Device acknowledgement of a queued downlink."""

    @property
    def queue_item_id(self) -> str: ...

    @property
    def acknowledged(self) -> bool: ...


class TxAck(Protocol):
    """Gateway acknowledgement of transmission."""

    @property
    def gateway_id(self) -> str: ...

    @property
    def downlink_id(self) -> int: ...


class Log(Protocol):
    """Device log message with numeric level and code."""

    @property
    def description(self) -> str: ...

    @property
    def level(self) -> int: ...

    @property
    def code(self) -> int: ...


class Coordinates(Protocol):
    """Location estimate."""

    @property
    def latitude(self) -> float: ...

    @property
    def longitude(self) -> float: ...

    @property
    def altitude(self) -> float: ...


class Location(Protocol):
    """Location event with a nested read-only view."""

    @property
    def location(self) -> Coordinates: ...


type Payload = Uplink | Join | Status | Ack | TxAck | Log | Location


@dataclass(frozen=True, slots=True)
class UplinkData:
    """Uplink fixture without a backend dependency."""

    data: bytes
    f_port: int = 1


@dataclass(frozen=True, slots=True)
class StatusData:
    """Status fixture preserving the battery-unavailable flag."""

    margin: int = 0
    external_power_source: bool = False
    battery_level_unavailable: bool = True
    battery_level: float = 0


@dataclass(frozen=True, slots=True)
class JoinData:
    """Join fixture."""

    dev_addr: str


@dataclass(frozen=True, slots=True)
class AckData:
    """Downlink acknowledgement fixture."""

    queue_item_id: str
    acknowledged: bool


@dataclass(frozen=True, slots=True)
class TxAckData:
    """Gateway transmission acknowledgement fixture."""

    gateway_id: str
    downlink_id: int


@dataclass(frozen=True, slots=True)
class LogData:
    """Device log fixture; level and code retain backend numeric values."""

    description: str
    level: int
    code: int


@dataclass(frozen=True, slots=True)
class CoordinatesData:
    """Location coordinates fixture."""

    latitude: float
    longitude: float
    altitude: float


@dataclass(frozen=True, slots=True)
class LocationData:
    """Location fixture containing a borrowed coordinates object."""

    location: Coordinates
