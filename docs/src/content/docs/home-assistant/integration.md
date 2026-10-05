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
for all supported devices on that network. Store its `DeviceManager` in `entry.runtime_data`.

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
| `connection_entry_id` | The selected LoRaWAN config entry's `entry_id`. Setup passes this to `lorawan.async_get_connection()` to obtain the shared connection. |
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

Use `lorawan.DeviceManager` to own the collection and its coordinators.
Pass the vendor collection and a factory that creates a coordinator from `hass`
and a device model. The factory initializes `data` and subscribes to model updates
before returning. The manager then notifies platforms.

The `sensecap_lorawan` import refers to the
[example device library](/lorawan-connection/getting-started/quickstart/#example-library).
Define `DOMAIN = "sensecap"` in `const.py`.

```python
from homeassistant.components.lorawan import (
    ConnectionUnavailable,
    DeviceManager,
    ProviderNotFound,
    async_get_connection,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from sensecap_lorawan import S2101, SenseCapDeviceCollection
from .const import DOMAIN
from .coordinator import SenseCapCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
type SenseCapConfigEntry = ConfigEntry[DeviceManager[S2101, SenseCapCoordinator]]
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> bool:
    """Forward all vendor events to one library collection."""
    try:
        connection = async_get_connection(hass, entry.data["connection_entry_id"])
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
    manager = entry.runtime_data = DeviceManager(
        hass,
        entry,
        collection=SenseCapDeviceCollection(connection),
        create_coordinator=SenseCapCoordinator,
    )
    entry.async_on_unload(manager.close)
    try:
        await manager.async_setup()
    except ConnectionUnavailable as error:
        raise ConfigEntryNotReady("LoRaWAN provider is not connected") from error
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> bool:
    """Unload entities; the manager releases the collection and coordinators."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

`manager.async_setup()` calls the collection's `async_setup()` to subscribe to
its vendors. Existing devices receive coordinators before setup returns.
Platforms use `subscribe_coordinator_added()` to receive these coordinators and
later additions. The manager closes the collection and retires its coordinators
when the config entry unloads.

If the connection disconnects, the integration schedules a reload. Setup fails with
`ConfigEntryNotReady` while the connection remains unavailable. A deleted connection entry raises
`ProviderNotFound`; report this with `ConfigEntryError` instead of retrying. The LoRaWAN integration
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
from typing import override

from homeassistant.components.lorawan import device_identifiers
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
            identifiers=device_identifiers(DOMAIN, self.data),
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

`data` holds the model, whose attributes change in place. Each notification calls
`async_set_updated_data()` with that model and updates the listening entities.
The manager owns the coordinator's lifetime. Passing `config_entry=None` avoids
retaining retired coordinators until entry unload. The manager schedules
`async_shutdown()` when the model leaves the collection, including during unload.

The coordinator exposes `device_info` for its entities. Use
`lorawan.device_identifiers(DOMAIN, device)` for its identifiers. The helper combines
the integration domain, network ID, and DevEUI. The manager uses the same identity
for cleanup without reading the coordinator's `device_info`.

## Device lifecycle

HA supplies config entries, coordinators, and a device registry. `lorawan-connection`
supplies model identity and collection notifications. The manager connects those
contracts so each vendor integration can use the same lifecycle handling:

- Create one coordinator per model and deliver it to all subscribed platforms.
- Remove the registry device when its model is removed or replaced. HA removes
  its entity registry records and active entities, including disabled entities.
- After successful setup, remove registry devices absent from the collection.
  This covers devices removed while HA was offline.
- Update registered device names when their model descriptors change.
- Close the collection and retire coordinators on unload, preserving registry records.

Entities register devices through their coordinator's `device_info`. Keep manufacturer
and model metadata in the vendor coordinator. The manager accepts ordinary
`DataUpdateCoordinator` subclasses; no LoRaWAN-specific coordinator base is required.

Cleanup is scoped to the vendor config entry. Failed setup preserves registry
records because an incomplete collection does not prove a device was removed.
Use the manager's `close()` as the entry unload callback. Do not also close the
collection or register separate coordinator-removal callbacks.

## A shared entity base

Use `lorawan.LoRaWANEntity` for entities backed by these device models. It extends
`CoordinatorEntity`, which manages update subscriptions. It also handles a queued
entity addition that finishes after the manager removed its device.

Its `async_update()` is a no-op because readings arrive through the connection.
Provider disconnection uses the config-entry reload path.

## Map device values to entities

This `sensor.py` subscribes to coordinators from the manager. Temperature and humidity
entities share a coordinator. `LoRaWANEntity` handles update subscriptions and late
entity additions. Entity descriptions select the model attributes to read.

Keep unknown measurements as `None`. A normally sleeping LoRaWAN device does not
become unavailable just because another uplink has not arrived yet.

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

Other platforms subscribe to the manager and receive the same coordinator instances.

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

Return a `MockConnection` from the provider's `async_get_connection()` helper and emit
events through it into the real vendor collection. Assert discovery confirmation, initial model replay, entity state,
later additions, removal, unload, and reload after disconnect.

Check that one model update reaches every entity through their shared coordinator.
Check live removal, startup cleanup of devices removed while offline, and disabled
entity cleanup. A failed setup and ordinary collection shutdown must preserve
registry records.

Decoder tests belong to the vendor library. The HA suite tests entity mapping and
lifecycle without a real network server. Keep tests against a real ChirpStack
server separate from the HA test suite.
