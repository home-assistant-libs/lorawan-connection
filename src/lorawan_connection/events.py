"""Immutable typed inventory and activity events."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal


class EventType(StrEnum):
    """Inventory and live activity types."""

    ADDED = "added"
    UPDATED = "updated"
    REMOVED = "removed"
    UPLINK = "up"
    JOIN = "join"
    STATUS = "status"
    ACK = "ack"
    TX_ACK = "txack"
    LOG = "log"
    LOCATION = "location"


@dataclass(frozen=True, slots=True)
class DeviceDescriptor:
    """Catalog identity and current inventory metadata."""

    network_id: str
    stack: str = field(kw_only=True)
    dev_eui: str
    name: str
    application_id: str
    profile_id: str
    model_id: str = ""
    brand_id: int | str | None = None
    model: str = ""
    manufacturer: str = ""

    def __post_init__(self) -> None:
        """Normalize and validate device identity."""
        eui = self.dev_eui.replace(":", "").lower()
        if len(eui) != 16 or any(c not in "0123456789abcdef" for c in eui):
            raise ValueError("DevEUI must contain exactly eight hexadecimal bytes")
        object.__setattr__(self, "dev_eui", eui)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Event:
    """Immutable event identity, derived from a descriptor when provided."""

    received_at: datetime
    network_id: str = ""
    dev_eui: str = ""
    descriptor: DeviceDescriptor | None = None

    def __post_init__(self) -> None:
        if self.descriptor is not None:
            if self.network_id and self.network_id != self.descriptor.network_id:
                raise ValueError("network_id conflicts with descriptor")
            if (
                self.dev_eui
                and self.dev_eui.replace(":", "").lower() != self.descriptor.dev_eui
            ):
                raise ValueError("dev_eui conflicts with descriptor")
            object.__setattr__(self, "network_id", self.descriptor.network_id)
            object.__setattr__(self, "dev_eui", self.descriptor.dev_eui)
        elif not self.network_id or not self.dev_eui:
            raise ValueError("network_id and dev_eui are required without a descriptor")
        eui = self.dev_eui.replace(":", "").lower()
        if len(eui) != 16 or any(c not in "0123456789abcdef" for c in eui):
            raise ValueError("DevEUI must contain exactly eight hexadecimal bytes")
        object.__setattr__(self, "dev_eui", eui)


@dataclass(frozen=True, slots=True, kw_only=True)
class _InventoryEvent:
    """Inventory identity is owned by its descriptor."""

    descriptor: DeviceDescriptor
    received_at: datetime

    @property
    def network_id(self) -> str:
        return self.descriptor.network_id

    @property
    def dev_eui(self) -> str:
        return self.descriptor.dev_eui


@dataclass(frozen=True, slots=True, kw_only=True)
class AddedEvent(_InventoryEvent):
    """A device is available to the subscriber."""

    type: Literal[EventType.ADDED] = field(default=EventType.ADDED, init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdatedEvent(_InventoryEvent):
    """A device descriptor changed."""

    type: Literal[EventType.UPDATED] = field(default=EventType.UPDATED, init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class RemovedEvent(_InventoryEvent):
    """A device left the subscription."""

    type: Literal[EventType.REMOVED] = field(default=EventType.REMOVED, init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class UplinkEvent(_Event):
    """Raw application bytes received from a device."""

    type: Literal[EventType.UPLINK] = field(default=EventType.UPLINK, init=False)
    data: bytes
    f_port: int = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class JoinEvent(_Event):
    """A device joined the network."""

    type: Literal[EventType.JOIN] = field(default=EventType.JOIN, init=False)
    dev_addr: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusEvent(_Event):
    """Device battery and radio link status."""

    type: Literal[EventType.STATUS] = field(default=EventType.STATUS, init=False)
    margin: int = 0
    external_power_source: bool = False
    battery_level_unavailable: bool = True
    battery_level: float = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class AckEvent(_Event):
    """Outcome of a confirmed downlink."""

    type: Literal[EventType.ACK] = field(default=EventType.ACK, init=False)
    queue_item_id: str
    acknowledged: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class TxAckEvent(_Event):
    """A gateway acknowledged transmission, not device receipt."""

    type: Literal[EventType.TX_ACK] = field(default=EventType.TX_ACK, init=False)
    gateway_id: str
    downlink_id: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LogEvent(_Event):
    """Backend log values retain their numeric level and code."""

    type: Literal[EventType.LOG] = field(default=EventType.LOG, init=False)
    description: str
    level: int
    code: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LocationEvent(_Event):
    """Device location estimate."""

    type: Literal[EventType.LOCATION] = field(default=EventType.LOCATION, init=False)
    latitude: float
    longitude: float
    altitude: float


type DeviceEvent = (
    AddedEvent
    | UpdatedEvent
    | RemovedEvent
    | UplinkEvent
    | JoinEvent
    | StatusEvent
    | AckEvent
    | TxAckEvent
    | LogEvent
    | LocationEvent
)
