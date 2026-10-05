"""Read-only event contracts and concrete inventory fixtures."""

from dataclasses import dataclass, field
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


@dataclass(frozen=True, slots=True, init=False)
class DeviceEventData:
    """Lightweight envelope for generated payloads and test fixtures."""

    network_id: str
    dev_eui: str
    type: EventType
    received_at: datetime
    descriptor: DeviceDescriptor | None = None
    data: Payload | None = None

    def __init__(
        self,
        *,
        type: EventType,
        received_at: datetime,
        descriptor: DeviceDescriptor | None = None,
        data: Payload | None = None,
        network_id: str | None = None,
        dev_eui: str | None = None,
    ) -> None:
        """Derive identity from a descriptor, or require explicit identifiers."""
        if descriptor is not None:
            if network_id is not None and network_id != descriptor.network_id:
                raise ValueError("network_id conflicts with descriptor")
            if (
                dev_eui is not None
                and dev_eui.replace(":", "").lower() != descriptor.dev_eui
            ):
                raise ValueError("dev_eui conflicts with descriptor")
            network_id = descriptor.network_id
            dev_eui = descriptor.dev_eui
        elif network_id is None or dev_eui is None:
            raise ValueError("network_id and dev_eui are required without a descriptor")

        object.__setattr__(self, "network_id", network_id)
        object.__setattr__(self, "dev_eui", dev_eui)
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "descriptor", descriptor)
        object.__setattr__(self, "data", data)
