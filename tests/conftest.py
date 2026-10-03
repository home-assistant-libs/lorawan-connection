"""Backend-free fixtures for the public collection contract."""

from datetime import UTC, datetime

import pytest

from lorawan_connection import DeviceDescriptor, DeviceEvent, DeviceEventData, EventType

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DESCRIPTOR = DeviceDescriptor(
    "network", "0201010101010101", "Sensor", "app", "profile", "model", 744
)


def inventory(
    descriptor: DeviceDescriptor = DESCRIPTOR, kind: EventType = EventType.ADDED
) -> DeviceEventData:
    return DeviceEventData(
        descriptor.network_id, descriptor.dev_eui, kind, NOW, descriptor
    )


class DeviceModel:
    def __init__(self, descriptor: DeviceDescriptor) -> None:
        self.descriptor = descriptor
        self.events: list[DeviceEvent] = []
        self.close_count = 0

    def handle_event(self, event: DeviceEvent) -> None:
        self.events.append(event)

    def close(self) -> None:
        self.close_count += 1


@pytest.fixture
def descriptor() -> DeviceDescriptor:
    return DESCRIPTOR
