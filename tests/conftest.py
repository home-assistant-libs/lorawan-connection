"""Backend-free fixtures for the public collection contract."""

from datetime import UTC, datetime

import pytest

from lorawan_connection import (
    Device,
    DeviceDescriptor,
    DeviceEvent,
    DeviceEventData,
    EventType,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DESCRIPTOR = DeviceDescriptor(
    "network", "0201010101010101", "Sensor", "app", "profile", "model", 744
)


def inventory(
    descriptor: DeviceDescriptor = DESCRIPTOR, kind: EventType = EventType.ADDED
) -> DeviceEventData:
    return DeviceEventData(type=kind, received_at=NOW, descriptor=descriptor)


class DeviceModel(Device):
    vendor_id = 744
    catalog_model_id = "model"

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        super().__init__(descriptor)
        self.events: list[DeviceEvent] = []
        self.close_count = 0

    def handle_event(self, event: DeviceEvent) -> None:
        self.events.append(event)

    def close(self) -> None:
        self.close_count += 1
        super().close()


@pytest.fixture
def descriptor() -> DeviceDescriptor:
    return DESCRIPTOR
