"""Read-only event contracts and concrete inventory fixtures."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .payloads import Payload


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
    dev_eui: str
    name: str
    application_id: str
    profile_id: str
    catalog_model_id: str = ""
    vendor_id: int | None = None
    model: str = ""
    manufacturer: str = ""

    def __post_init__(self) -> None:
        """Normalize and validate device identity."""
        eui = self.dev_eui.replace(":", "").lower()
        if len(eui) != 16 or any(c not in "0123456789abcdef" for c in eui):
            raise ValueError("DevEUI must contain exactly eight hexadecimal bytes")
        object.__setattr__(self, "dev_eui", eui)


class DeviceEvent(Protocol):
    """Borrowed read-only event; payload remains owned by the producer."""

    @property
    def network_id(self) -> str: ...

    @property
    def dev_eui(self) -> str: ...

    @property
    def type(self) -> EventType: ...

    @property
    def received_at(self) -> datetime: ...

    @property
    def descriptor(self) -> DeviceDescriptor | None: ...

    @property
    def data(self) -> Payload | None: ...


@dataclass(frozen=True, slots=True)
class DeviceEventData:
    """Lightweight envelope for generated payloads and test fixtures."""

    network_id: str
    dev_eui: str
    type: EventType
    received_at: datetime
    descriptor: DeviceDescriptor | None = None
    data: Payload | None = None
