"""Backend-free fixtures for the public collection contract."""

from datetime import UTC, datetime

import pytest

from lorawan_connection import (
    AddedEvent,
    Device,
    DeviceDescriptor,
    DeviceEvent,
    EventType,
    RemovedEvent,
    UpdatedEvent,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DESCRIPTOR = DeviceDescriptor(
    "network",
    "0201010101010101",
    "Sensor",
    "app",
    "profile",
    "model",
    744,
    stack="chirpstack",
)


def inventory(
    descriptor: DeviceDescriptor = DESCRIPTOR, kind: EventType = EventType.ADDED
) -> DeviceEvent:
    return {
        EventType.ADDED: AddedEvent,
        EventType.UPDATED: UpdatedEvent,
        EventType.REMOVED: RemovedEvent,
    }[kind](received_at=NOW, descriptor=descriptor)


class DeviceModel(Device):
    identifiers = {"chirpstack": (744, "model")}

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
