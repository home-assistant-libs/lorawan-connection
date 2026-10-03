---
title: Integration structure
description: Forward provider events to a library collection and observe its models from entities.
---

:::note[Proposed HA API]
These examples use the proposed `lorawan` provider API, which is not yet part of
Home Assistant. Device-library imports refer to the examples in this documentation.
:::

Connect a device collection to the provider. Read its model state from entities.
The HA integration manages config entries and subscriptions; the library selects
models and decodes their data.

Use one vendor config entry per provider network. That entry owns one collection
for all supported devices on that network. Store it in `entry.runtime_data`.
A later add-device or provisioning flow can use this entry; its UX is not defined
by the Python library.

## Dependencies and discovery

Declare the HA `lorawan` integration and your Python vendor library as dependencies.
The vendor library depends on `lorawan-connection`. Store `provider_entry_id` and
the stable `network_id` in the vendor config entry. The provider keeps the endpoint
and API key.

The provider discovers a vendor integration from recognized catalog identities.
The discovery flow confirms one entry for that network. Its unique ID must remain
stable when the server key changes. See [provider and discovery](/lorawan-connection/home-assistant/provider/)
for current limitations and the subscription signature.

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

## Platform setup

Listen for library device additions. Each entity listens for its own model's removal. Put this in `sensor.py`, together
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

The removal listener runs after the model closes. The entity becomes unavailable
while its removal task runs. If removal arrives before entity setup finishes,
the `device.closed` check removes the entity without registering listeners.

This example removes active entities but preserves registry records. This protects user
customizations if a device returns. Permanent registry cleanup and manual exclusion
need an agreed policy before upstream inclusion.

## Entity mapping

An entity reads typed model state and subscribes while it is loaded. It never
checks `EventType`, FPort, or protobuf classes.

```python
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from sensecap_lorawan import S2101


class SenseCapTemperature(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, device: S2101) -> None:
        self.device = device
        descriptor = device.descriptor
        identity = f"{descriptor.network_id}:{descriptor.dev_eui}"
        self._attr_unique_id = f"{identity}:channel_1:temperature"
        self._attr_device_info = DeviceInfo(
            identifiers={("sensecap", identity)},
            name=descriptor.name,
            manufacturer="Seeed Studio",
            model="SenseCAP S2101",
        )

    @property
    def native_value(self) -> float | None:
        return self.device.temperature

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
```

Add humidity the same way, using `SensorDeviceClass.HUMIDITY` and `PERCENTAGE`.
Keep unknown measurements as `None`. Do not mark a normally sleeping LoRaWAN device
unavailable just because it has not sent another uplink yet.

## Test the integration boundary

Mock the provider subscription and feed real shared events into the real vendor
collection. Assert discovery confirmation, initial model replay, entity state,
later additions, removal, unload, and reload after disconnect.

Decoder tests belong to the vendor library. The HA suite tests entity mapping and
lifecycle without a real network server. Keep tests against a real ChirpStack server separate from the HA test suite.

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
