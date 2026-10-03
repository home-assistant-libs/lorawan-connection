---
title: Integration structure
description: Forward provider events to a library collection and observe its models from entities.
---

:::note[Current HA status]
The LoRaWAN provider and SenseCAP integration exist on the
[Core POC branch](https://github.com/balloobbot/core/tree/lorawan-poc).
The provider subscription shown here is not yet an upstream Home Assistant API.
The branch currently vendors the shared library; the examples below use its
standalone package name. Discovery registration still needs an agreed HA hook.
:::

Forward provider events to the device library. Read its model state from entities.
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

The following setup pattern matches the POC. The `sensecap_lorawan` import refers
to a vendor library, represented by the tested example in this repository.

```python
from homeassistant.components import lorawan
from homeassistant.components.lorawan import ConnectionUnavailable
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from sensecap_lorawan import VENDOR_ID, SenseCapDeviceCollection

type SenseCapConfigEntry = ConfigEntry[SenseCapDeviceCollection]
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> bool:
    devices = entry.runtime_data = SenseCapDeviceCollection(
        network_id=entry.data["network_id"]
    )

    @callback
    def disconnected() -> None:
        hass.config_entries.async_schedule_reload(entry.entry_id)

    try:
        stop = await lorawan.async_subscribe(
            hass,
            entry.data["provider_entry_id"],
            frozenset({VENDOR_ID}),
            devices.handle_event,
            disconnected,
        )
    except ConnectionUnavailable as error:
        devices.close()
        raise ConfigEntryNotReady("LoRaWAN provider is not connected") from error

    entry.async_on_unload(stop)
    entry.async_on_unload(devices.close)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

Subscription sends initial inventory before returning. Models can therefore exist
before platform setup. `subscribe_device_added` replays them to each platform.
No per-device provider subscription or inventory lookup is needed.

If the provider disconnects, the integration schedules a reload. Setup fails with
`ConfigEntryNotReady` while the provider remains unavailable. The provider owns
transport recovery and credential reauthentication.

## Platform setup

Listen for library device additions and removals. The temperature-only example
below assumes a `SenseCapTemperature` entity defined in the next section.

```python
from homeassistant.core import callback


async def async_setup_entry(hass, entry, async_add_entities):
    entities = {}

    @callback
    def added(device):
        entity = entities[device.descriptor.dev_eui] = SenseCapTemperature(device)
        async_add_entities([entity])

    @callback
    def removed(device):
        entity = entities.pop(device.descriptor.dev_eui, None)
        if entity is not None and entity.hass is not None:
            entity.async_write_ha_state()
            entry.async_create_task(
                hass, entity.async_remove(force_remove=True), "Remove LoRaWAN entity"
            )

    entry.async_on_unload(entry.runtime_data.subscribe_device_added(added))
    entry.async_on_unload(entry.runtime_data.subscribe_device_removed(removed))
```

Removal happens after model `close()`. An entity that reads `device.closed` becomes
unavailable even before its removal task finishes. Production integrations must
also handle removal while an entity is still being added.

The POC removes active entities but preserves registry records. This protects user
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
        self.async_on_remove(self.device.add_update_listener(self.async_write_ha_state))
```

Add humidity the same way, using `SensorDeviceClass.HUMIDITY` and `PERCENTAGE`.
Keep unknown measurements as `None`. Do not mark a normally sleeping LoRaWAN device
unavailable just because it has not sent another uplink yet.

## Test the integration boundary

Mock the provider subscription and feed real shared events into the real vendor
collection. Assert discovery confirmation, initial model replay, entity state,
later additions, removal, unload, and reload after disconnect.

Decoder tests belong to the vendor library. The HA suite tests entity mapping and
lifecycle without a real network server. The POC's real ChirpStack test remains a
separate external test.

## Writable devices

The POC's Dragino integration passes a sender bound to its provider config entry:

```python
from functools import partial

models = DraginoDevices(
    network_id=entry.data["network_id"],
    send_downlink=partial(
        lorawan.async_send_downlink, hass, entry.data["provider_entry_id"]
    ),
)
```

Its switch entities call `await device.async_set_relay(channel, on)` inside
`asyncio.timeout(30)`. The library encodes the command and waits for its device ACK.
Entities read `device.relays[channel]` and observe the same update listener used
by sensor models. Device reports update relay state. Convert `DownlinkError` and
`TimeoutError` to a `HomeAssistantError` so a failed command reaches the caller.

Keep switches visible when the configured key is read-only. Fail the requested
write with a permission error; do not reject setup or mark the whole network offline.
