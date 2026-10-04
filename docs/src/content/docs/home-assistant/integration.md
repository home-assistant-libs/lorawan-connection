---
title: Integration structure
description: Share device updates through a coordinator and handle removal with a common entity base.
---

:::note[Proposal]
This is a proposal and is not part of Home Assistant yet.
:::

Build the [device library](/lorawan-connection/patterns/library/) first. For a Core
integration, publish it as a separate PyPI package with no Home Assistant imports.
Test its decoders, models, and commands independently.

The Home Assistant integration connects that library to config entries and entities.
The `lorawan` provider owns the server connection. Your device library consumes a
`Connection`, selects device models, and decodes their data. Entities read the
models' attributes and call their command methods.

Use one vendor config entry per provider network. That entry owns one collection
for all supported devices on that network. Store it in `entry.runtime_data`.

## Config flow

The user first configures the `lorawan` integration. This creates a Home Assistant
config entry for the server connection. That entry stores the endpoint and API key.

Your vendor integration's config flow selects which LoRaWAN entry to use.
For automatic discovery, the provider supplies its entry ID in the discovery data.
For manual setup, select the entry automatically when only one exists. Show a
chooser when several exist. If none exists, ask the user to set up LoRaWAN first.

After confirmation, store these fields in the vendor entry's `data`:

| Field | Meaning |
| --- | --- |
| `provider_entry_id` | The selected LoRaWAN config entry's `entry_id`. Setup passes this to `lorawan.get_connection()` to obtain the shared connection. |
| `network_id` | The provider's stable identifier for the logical network, supplied through discovery or read from the selected entry. |

Use `network_id` as the vendor entry's unique ID to prevent duplicate entries for
the same network. It stays unchanged when server credentials change. Connection
credentials remain in the LoRaWAN entry.

Users provision devices in the existing LoRaWAN stack in the first version.

## Dependencies and discovery

In `manifest.json`, put the HA `lorawan` integration in `dependencies`. Put your
Python vendor library in `requirements`, pinned to an exact published version.
The vendor library declares `lorawan-connection` in its own package dependencies
without a version constraint. Home Assistant's `lorawan` integration pins the
version used by all vendor libraries.

Declare one or more vendor IDs in the proposed `lorawan` manifest field. The
provider matches those IDs against recognized catalog identities to discover the
integration. For example, a SenseCAP manifest with an illustrative library requirement:

```json
{
  "dependencies": ["lorawan"],
  "lorawan": [744],
  "requirements": ["sensecap-lorawan==0.1.0"]
}
```

Use numeric LoRa Alliance VendorIDs; a match on any listed ID selects the integration.

## Config-entry setup

The `sensecap_lorawan` import refers to the
[example device library](/lorawan-connection/getting-started/quickstart/#example-library).

```python
from homeassistant.components import lorawan
from homeassistant.components.lorawan import ConnectionUnavailable
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from sensecap_lorawan import SenseCapDeviceCollection

type SenseCapConfigEntry = ConfigEntry[SenseCapDeviceCollection]
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> bool:
    try:
        connection = lorawan.get_connection(hass, entry.data["provider_entry_id"])
    except ConnectionUnavailable as error:
        raise ConfigEntryNotReady("LoRaWAN provider is not connected") from error
    entry.async_on_unload(
        connection.on_disconnect(
            lambda: hass.config_entries.async_schedule_reload(entry.entry_id)
        )
    )
    devices = entry.runtime_data = SenseCapDeviceCollection(connection)
    entry.async_on_unload(devices.close)
    try:
        await devices.async_setup()
    except ConnectionUnavailable as error:
        raise ConfigEntryNotReady("LoRaWAN provider is not connected") from error
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

`devices.async_setup()` receives existing devices before returning. Models can therefore exist
before platform setup. `subscribe_device_added` replays them to each platform.
The collection owns its event subscription.

If the provider disconnects, the integration schedules a reload. Setup fails with
`ConfigEntryNotReady` while the provider remains unavailable. The provider owns
transport recovery and credential reauthentication.

## Share updates through a coordinator

Use one `DataUpdateCoordinator` per physical device. All entities for that device
share it. The coordinator subscribes once to the model and distributes updates
to its entities. Keep decoding in the device library.

The [Modbus guide](https://home-assistant-libs.github.io/modbus-connection/home-assistant/integration/)
uses coordinators to poll device data. LoRaWAN receives updates from the server,
so this coordinator calls `async_set_updated_data()` without a polling interval
or a first refresh. Put this in `coordinator.py`:

```python
import logging

from sensecap_lorawan import S2101

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import SenseCapConfigEntry

_LOGGER = logging.getLogger(__name__)


class SenseCapCoordinator(DataUpdateCoordinator[S2101]):
    def __init__(
        self, hass: HomeAssistant, entry: SenseCapConfigEntry, device: S2101
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=device.descriptor.name)
        self.async_set_updated_data(device)
        entry.async_on_unload(device.add_update_listener(self._async_device_updated))

    @callback
    def _async_device_updated(self) -> None:
        self.async_set_updated_data(self.data)
```

`data` holds the model, whose attributes change in place. Each notification calls
`async_set_updated_data()` with that model and updates the listening entities.
Device removal clears the model's update listeners. Entry unload also unregisters
the subscription and shuts down the coordinator.

## A shared entity base

The proposed `lorawan.LoRaWANEntity` is a base for device entities. It extends Home
Assistant's `CoordinatorEntity` and handles model removal for every platform.
The provider would export this class for vendor integrations:

```python
from lorawan_connection import Device

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)


class LoRaWANEntity[DeviceT: Device](CoordinatorEntity[DataUpdateCoordinator[DeviceT]]):
    _attr_has_entity_name = True

    @property
    def device(self) -> DeviceT:
        return self.coordinator.data

    @property
    def available(self) -> bool:
        return super().available and not self.device.closed

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.device.add_remove_listener(self._async_device_removed)
        )

    @callback
    def _async_device_removed(self) -> None:
        self.async_write_ha_state()
        self.hass.async_create_task(self.async_remove(force_remove=True))

    async def async_update(self) -> None:
        """Values arrive through the library's subscription."""
```

`CoordinatorEntity` manages the entity's update subscription. `LoRaWANEntity`
adds a removal listener and unregisters it when the entity unloads. The model
closes before notifying removal, so the entity becomes unavailable while its
removal task runs.

The callback is synchronous, so it schedules `self.async_remove()` as a task.
This removes the active entity and preserves its registry record, including user
customizations. Permanent registry cleanup needs a separate policy.

Closing a collection during entry unload clears model listeners without reporting
device removal. Home Assistant unloads the entities through their platforms.
The `async_update()` override does not request a fresh reading; data arrives
through the subscription. Provider disconnection uses the config-entry reload
path shown above.

## Map device values to entities

This `sensor.py` creates a coordinator for each device. Temperature and humidity
entities share it and inherit removal handling from `LoRaWANEntity`. Entity
descriptions select the model attributes to read.

Keep unknown measurements as `None`. A normally sleeping LoRaWAN device does not
become unavailable just because another uplink has not arrived yet.

```python
from collections.abc import Callable
from dataclasses import dataclass

from sensecap_lorawan import S2101

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
from .coordinator import SenseCapCoordinator


@dataclass(frozen=True, kw_only=True)
class SenseCapSensorDescription(SensorEntityDescription):
    value_fn: Callable[[S2101], float | None]


SENSORS = (
    SenseCapSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda device: device.temperature,
    ),
    SenseCapSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda device: device.humidity,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SenseCapConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    @callback
    def added(device: S2101) -> None:
        coordinator = SenseCapCoordinator(hass, entry, device)
        async_add_entities(
            SenseCapSensor(coordinator, description) for description in SENSORS
        )

    entry.async_on_unload(entry.runtime_data.subscribe_device_added(added))


class SenseCapSensor(LoRaWANEntity[S2101], SensorEntity):
    entity_description: SenseCapSensorDescription
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: SenseCapCoordinator,
        description: SenseCapSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        descriptor = self.device.descriptor
        identity = f"{descriptor.network_id}:{descriptor.dev_eui}"
        self._attr_unique_id = f"{identity}:channel_1:{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={("sensecap", identity)},
            name=descriptor.name,
            manufacturer="Seeed Studio",
            model="SenseCAP S2101",
        )

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.device)
```

Pass the same coordinator to the device's other sensor entities. If it serves
several platforms, create coordinators in config-entry setup and share them
through `entry.runtime_data`. Each device still has its own coordinator.

## Writable devices

Use the same coordinator and entity setup pattern for writable devices. The
[Dragino device library](/lorawan-connection/modelling/overview/) supplies the
model and command methods. Entity setup assigns `self.channel` to the relay
number. The command methods call the model:

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
                "Timed out waiting for device acknowledgement"
            ) from error
        except DownlinkError as error:
            raise HomeAssistantError(str(error)) from error
```

The model encodes the command and waits for its device acknowledgement. The entity
bounds that wait with a 30-second timeout and reports failures as `HomeAssistantError`.
Its state comes from `device.relays`, which changes when the device reports new values.
Command completion does not set the switch state optimistically.

Keep switches visible with a read-only API key. Attempted writes report a permission
error without rejecting setup or marking the whole network offline.

## Test the integration boundary

Return a `MockConnection` from the provider's `get_connection()` helper and emit
events through it into the real vendor collection. Assert discovery confirmation, initial model replay, entity state,
later additions, removal, unload, and reload after disconnect.

Check that one model update reaches every entity through their shared coordinator.
Check device removal and listener cleanup when an entity unloads. Collection
shutdown must not report device removal.

Decoder tests belong to the vendor library. The HA suite tests entity mapping and
lifecycle without a real network server. Keep tests against a real ChirpStack
server separate from the HA test suite.
