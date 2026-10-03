---
title: Integration structure
description: Connect a device library to Home Assistant, with direct entity updates or a shared coordinator.
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

## Dependencies and discovery

Declare the HA `lorawan` integration and your Python vendor library as dependencies.
The vendor library depends on `lorawan-connection`. Store `provider_entry_id` and
the stable `network_id` in the vendor config entry. The provider keeps the endpoint
and API key.

Declare one or more vendor IDs in the proposed `lorawan` manifest field. The
provider matches those IDs against recognized catalog identities to discover the
integration. For example, a SenseCAP manifest includes:

```json
{
  "dependencies": ["lorawan"],
  "lorawan": [744]
}
```

Use numeric LoRa Alliance VendorIDs; a match on any listed ID selects the integration.
The discovery flow confirms one entry for that network. Its unique ID must remain
stable when the server key changes.

In a manual config flow, select the LoRaWAN provider automatically when there is
only one. Show a chooser when several exist. Prevent a second vendor entry for
the same provider. Users provision devices in the existing LoRaWAN stack in the
first version.

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

## Choose how entities receive updates

For a device with a few values, let each entity subscribe to the model's updates.
For devices with many related values or shared processing, use one
`DataUpdateCoordinator` per device and let its entities share it. The amount of
shared work matters more than a fixed number of entities.

The [Modbus guide](https://home-assistant-libs.github.io/modbus-connection/home-assistant/integration/)
uses coordinators to poll device data. Here the library receives
updates from the server, so the coordinator uses `async_set_updated_data()` without
a polling interval or a first refresh. Keep decoding in the device library in both
approaches.

Handle device removal in the entity in both cases. An entity knows how to remove
itself from Home Assistant; a coordinator only distributes updates. Put this
common base in `entity.py`:

```python
from sensecap_lorawan import S2101

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity


class SenseCapEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: S2101) -> None:
        self.device = device
        descriptor = device.descriptor
        self._identity = f"{descriptor.network_id}:{descriptor.dev_eui}"
        self._attr_device_info = DeviceInfo(
            identifiers={("sensecap", self._identity)},
            name=descriptor.name,
            manufacturer="Seeed Studio",
            model="SenseCAP S2101",
        )

    @property
    def available(self) -> bool:
        return not self.device.closed

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.device.closed:
            self.hass.async_create_task(self.async_remove(force_remove=True))
            return
        self.async_on_remove(
            self.device.add_remove_listener(self._async_device_removed)
        )

    @callback
    def _async_device_removed(self) -> None:
        self.async_write_ha_state()
        self.hass.async_create_task(self.async_remove(force_remove=True))
```

The removal listener runs after the model closes. The entity becomes unavailable
while its removal task runs. The callback is synchronous, so it schedules
`self.async_remove()` as a task. The `device.closed` check also handles a device
removed before entity setup finishes.

`async_on_remove()` unsubscribes when the entity leaves Home Assistant. Closing a
collection during entry unload clears model listeners without reporting device
removal; Home Assistant unloads the entities through their platforms.

These examples remove active entities and preserve registry records, including
user customizations. Permanent registry cleanup needs a separate policy.

## Direct entity subscriptions

Listen for library device additions. Put this in `sensor.py`, together
with the `SenseCapTemperature` entity from the next section. It imports
`SenseCapConfigEntry` from the integration's `__init__.py` shown above.

```python
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from sensecap_lorawan import S2101

from . import SenseCapConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SenseCapConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    @callback
    def added(device: S2101) -> None:
        async_add_entities([SenseCapTemperature(device)])

    entry.async_on_unload(entry.runtime_data.subscribe_device_added(added))
```

An entity reads typed model state and subscribes while it is loaded. It never
checks `EventType`, FPort, or protobuf classes.

```python
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from sensecap_lorawan import S2101

from .entity import SenseCapEntity


class SenseCapTemperature(SenseCapEntity, SensorEntity):
    _attr_translation_key = "temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, device: S2101) -> None:
        super().__init__(device)
        self._attr_unique_id = f"{self._identity}:channel_1:temperature"

    @property
    def native_value(self) -> float | None:
        return self.device.temperature

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if not self.device.closed:
            self.async_on_remove(
                self.device.add_update_listener(self.async_write_ha_state)
            )
```

Add humidity the same way, using `SensorDeviceClass.HUMIDITY` and `PERCENTAGE`.
Keep unknown measurements as `None`. Do not mark a normally sleeping LoRaWAN device
unavailable just because it has not sent another uplink yet.

## Share updates through a coordinator

A coordinator subscribes once to a device and notifies its entities. This is also
where shared Home Assistant processing belongs, if several entities need it.
The following alternative uses the same S2101 model so the two approaches can be
compared. Put this in `coordinator.py`:

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

Replace the direct-subscription `sensor.py` with this version. It reuses
`SenseCapEntity` for device identity and removal. `CoordinatorEntity` handles the
entity's update subscription and its cleanup. Entity descriptions map temperature
and humidity onto two entities that share one coordinator.

```python
from collections.abc import Callable
from dataclasses import dataclass

from sensecap_lorawan import S2101

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SenseCapConfigEntry
from .coordinator import SenseCapCoordinator
from .entity import SenseCapEntity


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


class SenseCapSensor(
    CoordinatorEntity[SenseCapCoordinator], SenseCapEntity, SensorEntity
):
    entity_description: SenseCapSensorDescription
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: SenseCapCoordinator,
        description: SenseCapSensorDescription,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        SenseCapEntity.__init__(self, coordinator.data)
        self.entity_description = description
        self._attr_unique_id = f"{self._identity}:channel_1:{description.key}"

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.device)

    @property
    def available(self) -> bool:
        return super().available and not self.device.closed

    async def async_update(self) -> None:
        """Values arrive through the library's subscription."""
```

Pass the same coordinator to the device's other sensor entities. If it serves
several platforms, create coordinators in config-entry setup and share them
through `entry.runtime_data`. Each device still has its own coordinator.

The `async_update()` override leaves manual refresh requests to the event feed;
this example has no method to request a fresh reading. Provider disconnection
uses the same config-entry reload path as direct entity subscriptions.

## Writable devices

Use the config-entry setup above with `DraginoDevices` and `Platform.SWITCH`.
The [Dragino device library](/lorawan-connection/modelling/overview/) supplies the
model and command methods. This `switch.py` creates one entity per relay:

```python
from asyncio import timeout
from typing import Any

from dragino_lorawan import DraginoDevices, LT22222
from lorawan_connection import DownlinkError

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[DraginoDevices],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    @callback
    def added(device: LT22222) -> None:
        async_add_entities(DraginoRelay(device, channel) for channel in (1, 2))

    entry.async_on_unload(entry.runtime_data.subscribe_device_added(added))


class DraginoRelay(SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: LT22222, channel: int) -> None:
        self.device = device
        self.channel = channel
        descriptor = device.descriptor
        identity = f"{descriptor.network_id}:{descriptor.dev_eui}"
        self._attr_unique_id = f"{identity}:relay_{channel}"
        self._attr_name = f"Relay {channel}"
        self._attr_device_info = DeviceInfo(
            identifiers={("dragino", identity)},
            name=descriptor.name,
            manufacturer="Dragino",
            model="LT-22222-L",
        )

    @property
    def is_on(self) -> bool | None:
        return self.device.relays[self.channel]

    @property
    def available(self) -> bool:
        return not self.device.closed

    async def async_added_to_hass(self) -> None:
        if self.device.closed:
            self.hass.async_create_task(self.async_remove(force_remove=True))
            return
        self.async_on_remove(self.device.add_update_listener(self.async_write_ha_state))
        self.async_on_remove(
            self.device.add_remove_listener(self._async_device_removed)
        )

    @callback
    def _async_device_removed(self) -> None:
        self.async_write_ha_state()
        self.hass.async_create_task(self.async_remove(force_remove=True))

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

Mock the provider subscription and feed real shared events into the real vendor
collection. Assert discovery confirmation, initial model replay, entity state,
later additions, removal, unload, and reload after disconnect.

For coordinator-based entities, check that one model update reaches every entity.
For both approaches, check removal before entity setup finishes and listener
cleanup when an entity unloads. Collection shutdown must not report device removal.

Decoder tests belong to the vendor library. The HA suite tests entity mapping and
lifecycle without a real network server. Keep tests against a real ChirpStack
server separate from the HA test suite.
