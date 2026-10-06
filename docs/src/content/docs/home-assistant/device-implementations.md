---
title: Device implementations
description: Share device updates through a coordinator and keep device and entity registries in sync.
---

:::note[Proposal]
This is a proposal and is not part of Home Assistant yet.
:::

A `DeviceManager` gives an integration access to matching devices on all available
LoRaWAN connections. It creates one collection per connection and one coordinator
per device, including connections registered later by
[connection providers](/lorawan-connection/home-assistant/connection-providers/).

Build and publish the [device library](/lorawan-connection/patterns/library/) first.
The examples use the [SenseCAP example library](/lorawan-connection/getting-started/quickstart/#example-library),
which is not a published package.

## Config flow

Users confirm the discovered vendor integration once. Use the vendor domain as its
unique ID to prevent duplicate entries. Setup can finish before any server connects.

## Dependencies and discovery

In `manifest.json`, put the HA `lorawan` integration in `dependencies`. Put your
Python vendor library in `requirements`, pinned to an exact published version.
The vendor library declares `lorawan-connection` in its own package dependencies
without a version constraint. Home Assistant's `lorawan` integration pins the
version used by all vendor libraries.

The proposed `lorawan` field lists stack and brand pairs used for discovery.
For SenseCAP:

```json
{
  "dependencies": ["lorawan"],
  "lorawan": [["chirpstack", 744], ["tts", "sensecap"]]
}
```

Use native brand IDs: ChirpStack uses integers; TTS uses strings.

Expose the library's supported models in a `lorawan.py` platform. The provider uses
this declaration to explain whether a device's model is supported:

```python
from sensecap_lorawan import SenseCapDeviceCollection

DEVICE_MODELS = SenseCapDeviceCollection.DEVICES
```

## Config-entry setup

Store the manager in `entry.runtime_data`. Its collection factory receives a
connection; its coordinator factory receives `hass` and a device model.
The coordinator must initialize `data` and subscribe to updates before returning.

In `__init__.py`, with `DOMAIN = "sensecap"` in `const.py`:

```python
from homeassistant.components.lorawan import DeviceManager
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from sensecap_lorawan import S2101, SenseCapDeviceCollection
from .const import DOMAIN
from .coordinator import SenseCapCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
type SenseCapConfigEntry = ConfigEntry[DeviceManager[S2101, SenseCapCoordinator]]
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> bool:
    """Set up supported devices across all LoRaWAN connections."""
    manager = entry.runtime_data = DeviceManager(
        hass,
        entry,
        create_collection=SenseCapDeviceCollection,
        create_coordinator=SenseCapCoordinator,
    )
    entry.async_on_unload(manager.close)
    await manager.async_setup()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> bool:
    """Unload entities; the manager releases the collection and coordinators."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

Existing devices have coordinators when `manager.async_setup()` returns.
Platforms subscribe through `subscribe_coordinator_added()`, which delivers current
coordinators immediately and future additions as they arrive.

A disconnected server makes its coordinators unavailable. The manager retains their
models and registry records, then reconciles inventory on reconnect. The server
integration handles recovery; the vendor entry stays loaded.

## Share updates through a coordinator

All entities for a device share a `DataUpdateCoordinator`. Model notifications
call `async_set_updated_data()`; no polling interval or first refresh is needed.
In `coordinator.py`:

```python
import logging
from typing import override

from homeassistant.components.lorawan import device_identifier
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from sensecap_lorawan import S2101
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SenseCapCoordinator(DataUpdateCoordinator[S2101]):
    """Distribute push updates from one physical device."""

    def __init__(self, hass: HomeAssistant, device: S2101) -> None:
        """Subscribe once to the library model."""
        # The device manager shuts down coordinators when their models retire.
        super().__init__(hass, _LOGGER, config_entry=None, name=device.descriptor.name)
        self.async_set_updated_data(device)
        self._unsubscribe = device.add_update_listener(self._async_device_updated)

    @property
    def device_info(self) -> DeviceInfo:
        """Describe this device for its entities."""
        return DeviceInfo(
            identifiers={device_identifier(DOMAIN, self.data)},
            name=self.data.descriptor.name,
            manufacturer="Seeed Studio",
            model="SenseCAP S2101",
        )

    @callback
    def _async_device_updated(self) -> None:
        """Forward the model's complete update to every entity."""
        self.async_set_updated_data(self.data)

    @override
    async def async_shutdown(self) -> None:
        """Release the model subscription when its collection retires it."""
        self._unsubscribe()
        await super().async_shutdown()
```

Passing `config_entry=None` lets the manager retire coordinators when their models
leave the collection, rather than retaining them until entry unload.

`device_identifier()` combines the vendor domain, server entry ID, and DevEUI.
Use it in `DeviceInfo.identifiers` so registry records match the manager's cleanup
identity. It returns one tuple, which must be wrapped in a set.

## Device lifecycle

`DeviceManager` handles registry cleanup and coordinator shutdown:

- Remove the registry device when its model is removed or replaced. HA removes
  its entity registry records and active entities, including disabled entities.
- After a connection registers, remove its registry devices absent from the complete
  device list. Keep records for other connections, including offline servers.
- Remove records for a deleted server entry, including deletions while the vendor
  integration was unloaded. Device identifiers carry the server entry ID.
- Update registered device names when their model descriptors change.
- Close the collection and retire coordinators on unload, preserving registry records.

Cleanup is scoped to the vendor entry. Failed setup preserves registry records
because incomplete inventory does not prove a device was removed.

Register only `manager.close` for collection and coordinator cleanup. It leaves
server transports open. The manager registers device identity before notifying
platforms; vendor coordinators supply manufacturer and model metadata.

## A shared entity base

`LoRaWANEntity` extends `CoordinatorEntity` with access to the model as `self.device`.
It marks closed models unavailable and prevents queued entity additions from
recreating a removed device. Its `async_update()` is a no-op; readings arrive
through subscriptions.

## Map device values to entities

Keep unknown measurements as `None`. Sleeping between uplinks does not make a
device unavailable. This `sensor.py` maps temperature and humidity to entities:

```python
from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.lorawan import LoRaWANEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SenseCapConfigEntry
from sensecap_lorawan import S2101
from .coordinator import SenseCapCoordinator

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SenseCapSensorDescription(SensorEntityDescription):
    """Select a typed measurement from the device model."""

    value_fn: Callable[[S2101], float | None]


DESCRIPTIONS = (
    SenseCapSensorDescription(
        key="temperature",
        value_fn=lambda device: device.temperature,
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SenseCapSensorDescription(
        key="humidity",
        value_fn=lambda device: device.humidity,
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SenseCapConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Listen for models, including those created during initial inventory."""

    @callback
    def added(coordinator: SenseCapCoordinator) -> None:
        async_add_entities(
            SenseCapSensor(coordinator, description) for description in DESCRIPTIONS
        )

    entry.async_on_unload(entry.runtime_data.subscribe_coordinator_added(added))


class SenseCapSensor(LoRaWANEntity[S2101], SensorEntity):
    """Read typed state; decoding and event interpretation belong to the library."""

    coordinator: SenseCapCoordinator
    entity_description: SenseCapSensorDescription

    def __init__(
        self, coordinator: SenseCapCoordinator, description: SenseCapSensorDescription
    ) -> None:
        """Bind one measurement to its model."""
        super().__init__(coordinator)
        self.entity_description = description
        descriptor = self.device.descriptor
        identity = f"{descriptor.network_id}:{descriptor.dev_eui}"
        self._attr_unique_id = f"{identity}:channel_1:{description.key}"

    @property
    @override
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    @override
    def native_value(self) -> float | None:
        """Return the last observed measurement."""
        return self.entity_description.value_fn(self.device)
```

## Writable devices

Call the model's command methods from the entity. This excerpt uses the
[Dragino model](/lorawan-connection/modelling/overview/); entity setup must assign
`self.channel` to the relay number:

```python
from asyncio import timeout
from typing import Any

from dragino_lorawan import LT22222
from lorawan_connection import DownlinkError

from homeassistant.components.lorawan import LoRaWANEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError


class DraginoRelay(LoRaWANEntity[LT22222], SwitchEntity):
    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_relay(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_relay(False)

    async def _async_set_relay(self, on: bool) -> None:
        try:
            async with timeout(30):
                await self.device.async_set_relay(self.channel, on)
        except TimeoutError as error:
            raise HomeAssistantError(
                translation_domain="dragino", translation_key="command_timeout"
            ) from error
        except DownlinkError as error:
            raise HomeAssistantError(
                translation_domain="dragino", translation_key="command_failed"
            ) from error
```

Read switch state from `device.relays`. An ACK confirms delivery; the next device
report supplies the resulting state.

Define `command_timeout` and `command_failed` under `exceptions` in `strings.json`.
Set `PARALLEL_UPDATES = 0` in `switch.py` so waiting for one device's acknowledgement
does not block commands to other devices.

Keep switches visible with a read-only API key. Attempted writes report a permission
error without rejecting setup or marking the whole network offline.

## Tests

Register server connections with mocked transports, then emit events into the real
vendor collections. Assert discovery confirmation, initial model replay, entity
state, later additions, removal, and unload. Test two connections together: losing
one must preserve the other’s availability, and reconnecting must reuse entities.

Include removal while offline, disabled entities, and updates shared by several
entities. Failed setup and ordinary shutdown must preserve registry records.
Keep decoder tests in the vendor library and real-server tests separate from the HA suite.
