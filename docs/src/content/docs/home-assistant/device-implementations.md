---
title: Device implementations
description: Build a Home Assistant integration for LoRaWAN devices.
---

A `DeviceManager` gives an integration access to matching devices on all available
LoRaWAN connections. [Connection integrations](/lorawan-connection/home-assistant/connection-integrations/)
configure the backends. Device integrations decode readings through a device library
and expose them as Home Assistant entities.

This guide follows the [SenseCAP integration in Core](https://github.com/home-assistant/core/tree/dev/homeassistant/components/sensecap_lorawan).
The examples use the published `sensecap-lorawan==1.0.0` package and current Core
setup and unload APIs. The sensor example includes three models to keep it short.

## Choose the library and device identities

Keep payload decoding and downlink encoding in a
[device library](/lorawan-connection/modelling/overview/). Entity properties read
its typed state. They do not parse packets or call a backend SDK.

Use the official catalog's brand and model identities on each supported stack.
A local device-profile UUID or display name does not identify a catalog model.
Test each advertised stack with its actual identifiers.

Use a domain such as `yourbrand_lorawan`. Replace `sensecap_lorawan` when adapting
these examples for another vendor.

## Dependencies and discovery

The manifest declares `lorawan` as a dependency and the vendor's stack/brand pairs
as discovery matchers. ChirpStack brand IDs are integers. TTS brand IDs are strings.
These are the relevant SenseCAP fields:

```json title="manifest.json fields"
{
  "domain": "sensecap_lorawan",
  "name": "SenseCAP",
  "config_flow": true,
  "dependencies": ["lorawan"],
  "integration_type": "hub",
  "iot_class": "local_push",
  "lorawan": [["chirpstack", 744], ["tts", "sensecap"]],
  "requirements": ["sensecap-lorawan==1.0.0"],
  "single_config_entry": true
}
```

Pin a published device library in `requirements`. Let Home Assistant's `lorawan`
integration select the shared `lorawan-connection` version. Avoid an upper bound
in the device library that prevents it from using Core's version.

The manifest discovers the vendor integration. The library selects device models.
Platform descriptions select the models and datapoints represented by entities.

## Confirm setup

Use one config entry for the vendor across all connections. Ask users to configure
a connection first, then require at least one device that the integration exposes.
Use `async_validate_devices()` for both checks. Validation releases its temporary
collections after checking the connections.

Derive the supported types from the entity descriptions. If the integration has
several platforms, include the types supported by each platform. Keep the library's
full device collection; filter models when adding entities.

```python title="const.py"
"""Constants for SenseCAP."""

DOMAIN = "sensecap_lorawan"
```

```python title="config_flow.py"
"""SenseCAP config flow."""

from typing import Any, override

from sensecap_lorawan import SenseCapDeviceCollection

from homeassistant.components.lorawan import (
    LoRaWANDiscoveryInfo,
    NoConnectionsError,
    NoSupportedDevicesError,
    async_validate_devices,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN
from .sensor import DESCRIPTIONS


class SenseCapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create one entry for all supported devices."""

    VERSION = 1

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup without choosing a server."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return await self.async_step_confirm(user_input)

    @override
    async def async_step_integration_discovery(
        self, discovery_info: LoRaWANDiscoveryInfo | dict[str, Any]
    ) -> ConfigFlowResult:
        """Discover the vendor once across all servers."""
        return await self.async_step_user()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm SenseCAP setup."""
        device_types = tuple(
            {
                device_type
                for description in DESCRIPTIONS
                for device_type in description.device_types
            }
        )
        try:
            await async_validate_devices(
                self.hass,
                create_collection=SenseCapDeviceCollection,
                is_supported=lambda device: isinstance(device, device_types),
            )
        except NoConnectionsError:
            return self.async_abort(reason="no_lorawan_connections")
        except NoSupportedDevicesError:
            return self.async_abort(reason="no_supported_devices")
        if user_input is not None:
            return self.async_create_entry(title="SenseCAP", data={})
        return self.async_show_form(step_id="confirm")
```

Keep the confirmation description short: “Set up SenseCAP devices from your
LoRaWAN connections.” Do not add an empty title placeholder. The integration name
already identifies the discovery card.

Core integrations reuse shared abort translations in `strings.json`:

```json title="strings.json config section"
{
  "config": {
    "abort": {
      "already_configured": "[%key:common::config_flow::abort::single_instance_allowed%]",
      "no_lorawan_connections": "[%key:component::lorawan::common::no_lorawan_connections%]",
      "no_supported_devices": "[%key:component::lorawan::common::no_supported_devices%]",
      "single_instance_allowed": "[%key:common::config_flow::abort::single_instance_allowed%]"
    },
    "step": {
      "confirm": {
        "description": "Set up SenseCAP devices from your LoRaWAN connections."
      }
    }
  }
}
```

## Set up the device manager

Store the manager in `entry.runtime_data` and register `manager.close` for unload.
A failed connection during setup raises `ConfigEntryNotReady` so Home Assistant
can retry. Entry setup and unload return `None` with the current Core API.

```python title="__init__.py"
"""Support SenseCAP devices over LoRaWAN."""

from lorawan_connection import ConnectionUnavailable
from sensecap_lorawan import SenseCapDevice, SenseCapDeviceCollection

from homeassistant.components.lorawan import DeviceManager
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import SenseCapCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
type SenseCapConfigEntry = ConfigEntry[
    DeviceManager[SenseCapDevice, SenseCapCoordinator]
]
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> None:
    """Set up SenseCAP devices across the available LoRaWAN connections."""
    manager = entry.runtime_data = DeviceManager(
        hass,
        entry,
        create_collection=SenseCapDeviceCollection,
        create_coordinator=SenseCapCoordinator,
    )
    entry.async_on_unload(manager.close)
    try:
        await manager.async_setup()
    except ConnectionUnavailable as err:
        raise ConfigEntryNotReady(str(err)) from err
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(hass: HomeAssistant, entry: SenseCapConfigEntry) -> None:
    """Unload SenseCAP entities."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
```

An existing entry can load before its connections become available. The manager
also handles connections and devices added later. Config-flow validation applies
when users create the entry; it does not block this startup behavior.

The manager owns collections, coordinator shutdown, and registry cleanup for
removed devices. Platforms create device registry entries through entity
`device_info`. Use the public helpers instead of maintaining a second device list
or adding registry listeners in each platform.

## Share updates and device metadata

Create one coordinator per model. Initialize its data and subscribe to model
notifications in the constructor. Push notifications update all entities for that
device. No polling interval or first refresh is needed.

```python title="coordinator.py"
"""SenseCAP device updates."""

import logging
from typing import override

from sensecap_lorawan import SenseCapDevice

from homeassistant.components.lorawan import device_identifier
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SenseCapCoordinator(DataUpdateCoordinator[SenseCapDevice]):
    """Coordinate SenseCAP device updates."""

    def __init__(self, hass: HomeAssistant, device: SenseCapDevice) -> None:
        """Subscribe once to the library model."""
        super().__init__(
            hass,
            _LOGGER,
            # DeviceManager owns the coordinator's lifecycle.
            config_entry=None,
            name=device.descriptor.name,
        )
        self.async_set_updated_data(device)
        self._unsubscribe = device.add_update_listener(self._async_device_updated)

    @property
    def device_info(self) -> DeviceInfo:
        """Describe this device for its entities."""
        return DeviceInfo(
            identifiers={device_identifier(DOMAIN, self.data)},
            name=self.data.descriptor.name,
            manufacturer="Seeed Studio",
            model=f"SenseCAP {type(self.data).__name__}",
            model_id=type(self.data).__name__,
        )

    @callback
    def _async_device_updated(self) -> None:
        """Update entities."""
        self.async_set_updated_data(self.data)

    @override
    async def async_shutdown(self) -> None:
        """Unsubscribe from device updates."""
        self._unsubscribe()
        await super().async_shutdown()
```

`device_identifier()` returns the identifier used by the manager's registry
cleanup and the LoRaWAN device overview. Wrap it in a set for `DeviceInfo`.
Include a useful manufacturer, model, and model ID, such as `Seeed Studio`,
`SenseCAP S2103`, and `S2103`. Keep user-assigned names intact.

`config_entry=None` gives `DeviceManager` control over coordinator lifetime.
Unload releases subscriptions without closing the connection integration's
transport. A lost connection makes its entities unavailable. A sleeping device
remains available between uplinks.

## Map readings to entities

Use generic descriptions with typed `value_fn` callables and a tuple of model
types. Share a description when models expose the same measurement. Filter in
`subscribe_coordinator_added()`, which supplies existing and future coordinators.

Pass the description to `LoRaWANEntity`. It sets the unique ID from the connection,
DevEUI, and description key. Keep keys stable, including channel numbers where
needed. Do not build a second unique ID in the entity constructor.

```python title="sensor.py"
"""SenseCAP sensors."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from sensecap_lorawan import (
    S2101,
    S2102,
    S2103,
    SenseCapDevice,
)

from homeassistant.components.lorawan import LoRaWANEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SenseCapConfigEntry
from .coordinator import SenseCapCoordinator

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SenseCapSensorDescription[DeviceT: SenseCapDevice](SensorEntityDescription):
    """Select a typed measurement from the device model."""

    device_types: tuple[type[DeviceT], ...]
    value_fn: Callable[[DeviceT], float | None]


DESCRIPTIONS: tuple[SenseCapSensorDescription[Any], ...] = (
    SenseCapSensorDescription[S2101 | S2103](
        key="temperature",
        device_types=(S2101, S2103),
        value_fn=lambda device: device.temperature,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SenseCapSensorDescription[S2101 | S2103](
        key="humidity",
        device_types=(S2101, S2103),
        value_fn=lambda device: device.humidity,
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SenseCapSensorDescription[S2102](
        key="illuminance",
        device_types=(S2102,),
        value_fn=lambda device: device.illuminance,
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SenseCapSensorDescription[SenseCapDevice](
        key="battery",
        device_types=(
            S2101,
            S2102,
            S2103,
        ),
        value_fn=lambda device: (
            battery_level
            if (battery_level := device.battery_level) is not None
            else device.battery_percentage
        ),
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SenseCapConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for existing and newly discovered devices."""

    @callback
    def added(coordinator: SenseCapCoordinator) -> None:
        async_add_entities(
            SenseCapSensor(coordinator, description)
            for description in DESCRIPTIONS
            if isinstance(coordinator.data, description.device_types)
        )

    entry.async_on_unload(entry.runtime_data.subscribe_coordinator_added(added))


class SenseCapSensor(LoRaWANEntity[SenseCapDevice], SensorEntity):
    """Home Assistant entity representing a SenseCAP device datapoint."""

    coordinator: SenseCapCoordinator
    entity_description: SenseCapSensorDescription[Any]

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

Use Home Assistant's default names and icons for standard device classes.
Temperature, humidity, illuminance, and battery need no custom translation key.
Use translated names for distinctions such as “Soil temperature” or “Probe 1
temperature”. Never include the device name in the entity name.

Match native units to the decoded value. For example, SenseCAP reports atmospheric
pressure in pascals. Set `native_unit_of_measurement=UnitOfPressure.PA` and, if
wanted, `suggested_unit_of_measurement=UnitOfPressure.HPA`. Changing the unit label
does not convert the reading.

Preserve unknown readings as `None`. Zero, `False`, and zero-percent battery are
valid readings. Use binary sensors for boolean state, with the matching device
class: door open, moisture detected, or a device-reported problem. Preserve `None`
in `is_on`; `bool(None)` incorrectly reports a known off state.

## Battery and diagnostic defaults

Always expose a battery percentage sensor for supported battery-powered devices,
even if their normal uplinks only contain voltage. Use the standard status value
from `Device.battery_level` first. The library stores status reports before it
calls the model's event handler. Models do not need to call `super().handle_event()`.

`battery_level` is already a percentage. It is `None` when status is absent,
battery level is unavailable, or the status reports external power. A device
library can supply a real uplink battery percentage as a fallback, as SenseCAP
does above. For a model with no such field, return `device.battery_level` directly.
Do not estimate percentage from voltage or convert a low-battery boolean into a
percentage. Do not create a battery entity for a mains-only socket.

Use these defaults for new entities:

| Datapoint | Category | Enabled by default |
| --- | --- | --- |
| Temperature, humidity, door state, leak state | Primary entity | Yes |
| Battery percentage | `EntityCategory.DIAGNOSTIC` | Yes |
| Battery voltage | `EntityCategory.DIAGNOSTIC` | No |
| Event count and last event duration | `EntityCategory.DIAGNOSTIC` | No |
| Optional external-probe readings | Depends on the measurement | No |

For the disabled diagnostics, set both `entity_category=EntityCategory.DIAGNOSTIC`
and `entity_registry_enabled_default=False`. An event counter can use
`SensorStateClass.TOTAL_INCREASING`. A last-event duration uses
`SensorDeviceClass.DURATION` and the actual time unit; it reports the duration of the last event.

Changing these defaults preserves existing user enable/disable choices. Document
how users can enable optional entities. A diagnostic entity category does not
require a `diagnostics.py` download platform.

## LoRaWAN details on the device page

The shared integration supplies connection, DevEUI, last-received timestamps,
gateway receptions, RSSI, SNR, and available status metadata. Device integrations
get this association through the manifest dependency and `device_identifier()`.
Use the shared device-page section instead of duplicating its data as attributes
on every sensor.

The section requires an administrator and a matching registered connection.
When it is missing, check the device ID in `lorawan/devices/list`, the loaded
manifest's `lorawan` dependency, and the browser's frontend version. Refresh the
frontend after an upgrade before changing registry identifiers.

The connection integration supplies its management-console URL when registering
with `lorawan`. A device integration does not need to construct that URL.

## Commands

Put byte encoding and command validation in the device library. Entity methods
call the model and translate errors into `HomeAssistantError`. Set
`PARALLEL_UPDATES = 0` so a sleeping device does not block another device's command.
For an entity with a model method `async_set_relay(channel, on)`, use:

```python title="Relay entity method"
async def _async_set_relay(self, on: bool) -> None:
    try:
        async with timeout(30):
            await self.device.async_set_relay(self.channel, on)
    except TimeoutError as error:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="command_timeout"
        ) from error
    except DownlinkError as error:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="command_failed"
        ) from error
```

Import `timeout` from `asyncio`, `DownlinkError` from `lorawan_connection`, and
`HomeAssistantError` from `homeassistant.exceptions`. Define both exception
translations. Give each channel a stable description key such as `relay_1`.

An acknowledgement confirms delivery, not the resulting device state. Update
state from device reports. A timeout stops waiting; it does not clear a queued
command. Queue expiry is a separate, optional command setting.

Accept monitoring with read-only credentials. A rejected write reports a service
error without making the whole connection unavailable.

## Brand images

Core integrations use the [Home Assistant brands repository](https://github.com/home-assistant/brands).
Use the manufacturer’s existing artwork and follow the
[brand image rules](https://contributors.home-assistant.io/core/integrations/brands/).

## Tests and CI

Use `MockConnection` from `lorawan_connection.mock` with the real device collection.
Register it through `async_register_connection()`. Use separate `connection_entry` and `config_entry` fixtures, as in
[SenseCAP's tests](https://github.com/home-assistant/core/tree/dev/tests/components/sensecap_lorawan).
Keep credentials and real network access out of unit tests.

This setup fragment creates an S2101 using its official TTS identity:

```python title="Connection fixture setup"
from homeassistant.components.lorawan import async_register_connection
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from lorawan_connection import DeviceDescriptor, Unsubscribe
from lorawan_connection.mock import MockConnection
from pytest_homeassistant.common import MockConfigEntry
from sensecap_lorawan import S2101


async def register_test_connection(
    hass: HomeAssistant,
) -> tuple[MockConnection, DeviceDescriptor, Unsubscribe]:
    """Register a connection containing one supported device."""
    await async_setup_component(hass, "lorawan", {})
    connection_entry = MockConfigEntry(domain="test_connection", entry_id="network")
    connection_entry.add_to_hass(hass)
    brand_id, model_id = S2101.identifiers["tts"]
    descriptor = DeviceDescriptor(
        network_id=connection_entry.entry_id,
        stack="tts",
        dev_eui="0201010101010101",
        name="Greenhouse",
        application_id="application",
        profile_id="local-profile",
        brand_id=brand_id,
        model_id=model_id,
    )
    connection = MockConnection([descriptor], stack_type="tts")
    unsubscribe = await async_register_connection(
        hass, connection_entry, connection=connection
    )
    return connection, descriptor, unsubscribe
```

Call `unsubscribe()` during fixture teardown. Emit `UplinkEvent` and `StatusEvent`
with explicit timestamps. Emit `AddedEvent` and `RemovedEvent` to test lifecycle.
Use manufacturer codec examples and anonymized device captures with expected
values. Include truncated packets, unknown channels, wrong ports, and partial
updates in the library's decoder tests.

Cover these integration behaviors:

- User setup and discovery, duplicate prevention, no connection, and no supported device.
- Supported catalog identities on every advertised backend.
- Unknown initial state, decoded values, units, default names, and stable unique IDs.
- Status battery before any uplink, zero percent, unavailable status, and external power.
- Diagnostic categories and disabled defaults, plus state updates after enabling an entity.
- Devices and connections added after setup; device removal also removes disabled entities.
- Two connections sharing a DevEUI; one connection failing leaves the other available.
- Reconnection, setup retry, unload, and shutdown without leaked subscriptions.
- Commands, acknowledgement failure, timeout, and read-only access where controls exist.
- LoRaWAN metadata linked to the registry device.

Use the current `pytest-home-assistant` helpers from Core. Ensure Core's generated
translations are available when asserting default entity names. Test disabled
defaults separately from tests that use the `entity_registry_enabled_by_default`
fixture.

Run the integration tests, formatting and lint checks, and hassfest before release.
Build the release from the tested commit. Record physical-radio tests separately
from codec fixtures and simulated connections.

## Review before publishing

Check the target Core version's SenseCAP integration and
[Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/).
Track applicable rules in `quality_scale.yaml` for a Core contribution. Confirm
setup validation, unique entries, runtime data, entity naming, translations,
unload, and tests. Mark rules that need further work as `todo`.

Leave configurable bridges unsupported until their attached sensors and uplink
formats can be identified. Do not guess a decoder from a device name or packet
length. Subentry flows currently support user setup and reconfiguration, but
[not discovery](https://contributors.home-assistant.io/core/integrations/subentries/).

## Custom integrations

### Domain and dependencies

A custom integration must not reuse `lorawan` or another Core integration's domain.
The `sensecap_lorawan` domain in these examples belongs to Core.

A custom integration can bundle its device library under `_vendor` instead.
Adjust its imports and use `requirements: []` when it needs no additional packages.
It still declares the `lorawan` dependency and uses Core's shared library.

### Translations

For custom integrations, supply literal translated text in `translations/en.json`
and matching source text in `strings.json`. Core's translation build resolves
`[%key:...%]` references; installing a custom integration does not run that build.
Use “Set up a LoRaWAN connection first” and “No supported devices were found on
your LoRaWAN connections” for the two validation errors.

### Brand images

Custom integrations include images in their own `brand` directory:

```text
custom_components/yourbrand_lorawan/
├── __init__.py
├── manifest.json
├── config_flow.py
├── const.py
├── coordinator.py
├── sensor.py
├── strings.json
├── translations/en.json
└── brand/
    ├── icon.png
    ├── logo.png
    ├── dark_icon.png       # Optional
    └── dark_logo.png       # Optional
```

Use the manufacturer's existing artwork and record its source. Keep icons square.
Include dark variants when the regular artwork is hard to read in dark mode.
The `@2x` variants are optional. Home Assistant serves these files through its
brand-image API; do not inject a frontend resource or add a manifest logo URL.
See the [brand image rules](https://contributors.home-assistant.io/core/integrations/brands/).

### Manifest and HACS

Custom manifests also need a `version`, `documentation`, `issue_tracker`, and
`codeowners`. Use the same integration version in the manifest and release tag.
Test the oldest supported Home Assistant release and current Core `dev`.

For HACS, put `hacs.json` at the repository root. This minimum includes 2026.11
betas; use it only if you test those versions:

```json title="hacs.json"
{
  "name": "Your Brand LoRaWAN",
  "homeassistant": "2026.11.0b0",
  "render_readme": true
}
```

Put the My Home Assistant HACS badge on the first line of the README, before the
title. Replace `OWNER` and `REPOSITORY` with the repository's actual names:

```markdown title="README.md opening"
[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=OWNER&repository=REPOSITORY&category=integration)

# Your Brand LoRaWAN

Use Your Brand devices with Home Assistant's LoRaWAN connections.
```

Follow the badge with the minimum Home Assistant version and installation steps:
download through HACS, restart, configure a LoRaWAN connection, register a device
with its official catalog identity, then confirm the discovered vendor integration.
Include manual installation under `config/custom_components` as an alternative.

List supported models, entities, and backends. Explain disabled diagnostics,
unknown initial values, and whether a physical device has been tested. Describe
capabilities directly; avoid claims such as “all devices supported” without model
and backend coverage. Keep payload and test-environment details in a development
section.

### Tests and CI

Test a pinned Core revision and current `dev`. Pin Core and its test helpers to
the same revision. Include discovery through the custom manifest and locally
served brand images in the integration tests.
