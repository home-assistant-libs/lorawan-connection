---
title: Integration structure
description: Share device updates through a coordinator and keep device and entity registries in sync.
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
integration. These discovery fields belong in a SenseCAP manifest:

```json
{
  "dependencies": ["lorawan"],
  "lorawan": [744]
}
```

Add a `requirements` entry for your published vendor package. The SenseCAP package
on this page is an example, not a published package.

Use numeric LoRa Alliance VendorIDs; a match on any listed ID selects the integration.

Expose the library's supported models in a `lorawan.py` platform. The provider uses
this declaration to explain whether a device's model is supported:

```python
from sensecap_lorawan import SenseCapDeviceCollection

DEVICE_MODELS = SenseCapDeviceCollection.DEVICES
```

## Config-entry setup

The `sensecap_lorawan` import refers to the
[example device library](/lorawan-connection/getting-started/quickstart/#example-library).

```python
from dataclasses import dataclass, field

from homeassistant.components.lorawan import (
    ConnectionUnavailable,
    ProviderNotFound,
    get_connection,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr

from sensecap_lorawan import S2101, SenseCapDeviceCollection
from .coordinator import SenseCapCoordinator

DOMAIN = "sensecap"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
type SenseCapConfigEntry = ConfigEntry[SenseCapData]
PLATFORMS = [Platform.SENSOR]


@dataclass
class SenseCapData:
    """Keep the collection and its shared device coordinators."""

    collection: SenseCapDeviceCollection
    coordinators: dict[str, SenseCapCoordinator] = field(default_factory=dict)


async def async_setup_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> bool:
    """Forward all vendor events to one library collection."""
    try:
        connection = get_connection(hass, entry.data["provider_entry_id"])
    except ProviderNotFound as error:
        raise ConfigEntryError("The selected LoRaWAN provider was removed") from error
    except ConnectionUnavailable as error:
        raise ConfigEntryNotReady("LoRaWAN provider is not connected") from error
    entry.async_on_unload(
        connection.on_disconnect(
            lambda: (
                None
                if hass.is_stopping
                else hass.config_entries.async_schedule_reload(entry.entry_id)
            )
        )
    )
    devices = SenseCapDeviceCollection(connection)
    entry.runtime_data = SenseCapData(devices)

    device_registry = dr.async_get(hass)

    @callback
    def added(device: S2101) -> None:
        entry.runtime_data.coordinators[device.descriptor.dev_eui] = (
            SenseCapCoordinator(hass, device)
        )

        descriptor = device.descriptor
        registered = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{descriptor.network_id}:{descriptor.dev_eui}")},
            name=descriptor.name,
            manufacturer="Seeed Studio",
            model="SenseCAP S2101",
        )

        @callback
        def update_name() -> None:
            device_registry.async_update_device(
                registered.id, name=device.descriptor.name
            )

        @callback
        def remove_device() -> None:
            device_registry.async_remove_device(registered.id)

        device.add_update_listener(update_name)
        device.add_remove_listener(remove_device)

    @callback
    def removed(device: S2101) -> None:
        coordinator = entry.runtime_data.coordinators.pop(device.descriptor.dev_eui)
        entry.async_create_task(
            hass, coordinator.async_shutdown(), "Stop device coordinator"
        )

    entry.async_on_unload(devices.subscribe_device_added(added))
    entry.async_on_unload(devices.subscribe_device_removed(removed))
    # Unload runs in reverse order: retire models before removing collection listeners.
    entry.async_on_unload(devices.close)
    try:
        await devices.async_setup()
    except ConnectionUnavailable as error:
        raise ConfigEntryNotReady("LoRaWAN provider is not connected") from error
    current = {
        (DOMAIN, f"{device.descriptor.network_id}:{device.descriptor.dev_eui}")
        for device in devices.devices.values()
    }
    for registered in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        if not registered.identifiers.intersection(current):
            device_registry.async_remove_device(registered.id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> bool:
    """Unload entities; entry callbacks release collection and subscription."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

`devices.async_setup()` receives existing devices before returning. Models can therefore exist
before platform setup. `subscribe_device_added` replays them to each platform.
The collection owns its event subscription. Setup creates a coordinator before
platform callbacks run. Removal drops that coordinator and schedules its shutdown.

If the provider disconnects, the integration schedules a reload. Setup fails with
`ConfigEntryNotReady` while the provider remains unavailable. A deleted provider raises
`ProviderNotFound`; report this with `ConfigEntryError` instead of retrying. The provider
owns transport recovery and credential reauthentication.

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

_LOGGER = logging.getLogger(__name__)


class SenseCapCoordinator(DataUpdateCoordinator[S2101]):
    def __init__(self, hass: HomeAssistant, device: S2101) -> None:
        super().__init__(hass, _LOGGER, config_entry=None, name=device.descriptor.name)
        self.async_set_updated_data(device)
        self._unsubscribe = device.add_update_listener(self._async_device_updated)

    @callback
    def _async_device_updated(self) -> None:
        self.async_set_updated_data(self.data)

    async def async_shutdown(self) -> None:
        self._unsubscribe()
        await super().async_shutdown()
```

`data` holds the model, whose attributes change in place. Each notification calls
`async_set_updated_data()` with that model and updates the listening entities.
The collection callbacks own the coordinator's lifetime. Passing `config_entry=None`
avoids retaining each coordinator until entry unload. The removal callback shuts
it down when its model leaves the collection, including when the collection closes.

## Remove devices and entities

Register each model in the device registry when the collection announces it.
Attach an `add_remove_listener()` callback that calls `async_remove_device()`.
Home Assistant then removes the device's entity registry records and active entities,
including disabled entities. A metadata update also updates the device's name.

After `await devices.async_setup()` succeeds, compare the collection with devices
registered to the config entry. Remove registry devices absent from the collection.
This catches devices deleted while Home Assistant was offline. Do not run this
cleanup after a failed subscription or an incomplete device-list read.

Ordinary collection shutdown does not call a model's removal listeners. Entry
unload therefore preserves registry records and user customizations.

## A shared entity base

The proposed `lorawan.LoRaWANEntity` extends `CoordinatorEntity`. Device removal
usually happens through the collection callbacks above. The base also handles an
entity whose queued addition completes after its model was removed:

```python
from typing import override

from lorawan_connection import Device

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)


class LoRaWANEntity[DeviceT: Device](CoordinatorEntity[DataUpdateCoordinator[DeviceT]]):
    """Share model updates; the collection owns device registry cleanup."""

    _attr_has_entity_name = True

    @property
    def device(self) -> DeviceT:
        """Return the model shared by this device's entities."""
        return self.coordinator.data

    @property
    @override
    def available(self) -> bool:
        return super().available and not self.device.closed

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Removal can race with an entity queued for addition by its platform.
        if self.device.closed and self.registry_entry and self.registry_entry.device_id:
            registry = dr.async_get(self.hass)
            if registry.async_get(self.registry_entry.device_id):
                registry.async_remove_device(self.registry_entry.device_id)

    @override
    async def async_update(self) -> None:
        """Make update_entity a no-op; values arrive through the subscription."""
```

`CoordinatorEntity` manages each entity's update subscription. The `async_update()`
override makes `homeassistant.update_entity` a no-op because readings arrive
through the connection. Provider disconnection uses the config-entry reload path.

## Map device values to entities

This `sensor.py` uses the coordinators created during setup. Temperature and humidity
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

PARALLEL_UPDATES = 0


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
        coordinator = entry.runtime_data.coordinators[device.descriptor.dev_eui]
        async_add_entities(
            SenseCapSensor(coordinator, description) for description in SENSORS
        )

    entry.async_on_unload(entry.runtime_data.collection.subscribe_device_added(added))


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

Other platforms use the same coordinator from `entry.runtime_data`.

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
                translation_domain="dragino", translation_key="command_timeout"
            ) from error
        except DownlinkError as error:
            raise HomeAssistantError(
                translation_domain="dragino", translation_key="command_failed"
            ) from error
```

The model encodes the command and waits for its device acknowledgement. The entity
bounds that wait with a 30-second timeout and reports failures as `HomeAssistantError`.
Its state comes from `device.relays`, which changes when the device reports new values.
Command completion does not set the switch state optimistically.

Define `command_timeout` and `command_failed` under `exceptions` in `strings.json`.
Set `PARALLEL_UPDATES = 0` in `switch.py` so waiting for one device's acknowledgement
does not block commands to other devices.

Keep switches visible with a read-only API key. Attempted writes report a permission
error without rejecting setup or marking the whole network offline.

## Test the integration boundary

Return a `MockConnection` from the provider's `get_connection()` helper and emit
events through it into the real vendor collection. Assert discovery confirmation, initial model replay, entity state,
later additions, removal, unload, and reload after disconnect.

Check that one model update reaches every entity through their shared coordinator.
Check live removal, startup cleanup of devices removed while offline, and disabled
entity cleanup. A failed setup and ordinary collection shutdown must preserve
registry records.

Decoder tests belong to the vendor library. The HA suite tests entity mapping and
lifecycle without a real network server. Keep tests against a real ChirpStack
server separate from the HA test suite.
