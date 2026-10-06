---
title: Connection providers
description: Configure a backend, register its connection with LoRaWAN, and own recovery and shutdown in Home Assistant.
---

:::note[Proposal]
The shared `lorawan` integration and registration API described here are proposed
Home Assistant APIs. They are implemented in the PoC, not released in Home Assistant.
:::

First implement and publish a backend using the
[backend guide](/lorawan-connection/connection/adding-a-backend/).
A connection provider is a server integration that configures a backend and
registers its connection with `lorawan`.
[Device implementations](/lorawan-connection/home-assistant/device-implementations/)
use these connections to create vendor entities through `DeviceManager`.

The examples below use the released TTS adapter. For a new server, substitute
your adapter, authentication exception, configuration fields, and dependency extra.
Keep server-specific SDK calls inside the Python backend.

## Declare the dependency

Add an integration directory with `manifest.json`, `__init__.py`, `config_flow.py`,
`const.py`, and `strings.json`. Follow Home Assistant's integration requirements
for ownership, documentation, tests, and the quality scale.

These are the dependency fields for a TTS server integration:

```json
{
  "config_flow": true,
  "dependencies": ["lorawan"],
  "integration_type": "hub",
  "iot_class": "cloud_push",
  "requirements": ["lorawan-connection[tts]==0.10.0"]
}
```

Add the integration's own domain, name, code owners, and documentation URL to the
manifest. Choose `iot_class` for the actual transport and deployment. Pin the
published version that provides your backend. The shared `lorawan` integration
pins the base library; its version and the server extra must agree.

A separately published adapter belongs in `requirements` under its own package
name and version. The HA integration imports its backend explicitly. Shared
LoRaWAN code and vendor integrations must not import optional server adapters.

## Configure and validate the server

The config flow collects the endpoint, credentials, and applications or tenant
that define its scope. Mark credentials as secret fields. Give each server/scope
a stable unique ID so repeated setup does not create duplicate entries. Do not
include the API key in that ID.

Validate the connection using the backend and always close the temporary client.
For TTS, a config flow can call this helper and display the returned error key:

```python
from lorawan_connection import ConnectionUnavailable
from lorawan_connection.backend.tts import AuthenticationError, TTSConnection


async def async_validate_server(
    endpoint: str,
    api_key: str,
    application_ids: list[str],
    identity_server: str | None = None,
) -> str | None:
    try:
        connection = TTSConnection(
            endpoint,
            api_key,
            application_ids=application_ids,
            identity_server=identity_server,
            network_id="validation",
        )
    except ValueError:
        return "invalid_input"
    try:
        await connection.async_connect()
    except AuthenticationError:
        return "invalid_auth"
    except ConnectionUnavailable:
        return "cannot_connect"
    finally:
        await connection.close()
    return None
```

Validate the input's shape and nonempty application selection before calling the
helper. Define corresponding form errors in `strings.json`. Do not require write
permission just to connect: read-only credentials should support monitoring.

Implement reauthentication to validate replacement credentials, update the same
config entry, and reload it. Preserve its entry ID and scope. Do not register the
validation client with `lorawan`; normal entry setup creates the owned connection.

## Connect, register, and close

The backend must support `async_subscribe(brands=None, callback=...)`. Registration
uses this to receive every device in the selected scope. The narrower consumer
`Connection` protocol is sufficient for vendor collections but not server registration.

Set `network_id=entry.entry_id`. LoRaWAN uses that identity to route events and
distinguish identical DevEUIs on different servers. Registration expects complete
initial inventory and returns an idempotent unsubscribe callback. It does not own
the transport.

This `__init__.py` example uses the fields `endpoint`, `api_key`, `application_ids`,
and optional `identity_server` stored by the config flow. Define the integration's
`DOMAIN` in `const.py`.

```python
from dataclasses import dataclass

from lorawan_connection import ConnectionUnavailable, Unsubscribe
from lorawan_connection.backend.tts import AuthenticationError, TTSConnection

from homeassistant.components import lorawan
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class ServerData:
    connection: TTSConnection
    unsubscribe: Unsubscribe
    unsubscribe_disconnect: Unsubscribe


type ServerConfigEntry = ConfigEntry[ServerData]


async def async_setup_entry(hass: HomeAssistant, entry: ServerConfigEntry) -> bool:
    connection = TTSConnection(
        entry.data["endpoint"],
        entry.data["api_key"],
        application_ids=entry.data["application_ids"],
        identity_server=entry.data.get("identity_server"),
        network_id=entry.entry_id,
    )
    try:
        await connection.async_connect()
        unsubscribe = await lorawan.async_register_connection(
            hass, entry, connection=connection
        )
    except AuthenticationError as error:
        await connection.close()
        raise ConfigEntryAuthFailed from error
    except ConnectionUnavailable as error:
        await connection.close()
        raise ConfigEntryNotReady from error
    except BaseException:
        await connection.close()
        raise
    entry.async_on_unload(unsubscribe)

    @callback
    def disconnected() -> None:
        if not hass.is_stopping:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    unsubscribe_disconnect = connection.on_disconnect(disconnected)
    entry.async_on_unload(unsubscribe_disconnect)
    entry.runtime_data = ServerData(connection, unsubscribe, unsubscribe_disconnect)

    async def async_stop(_: Event) -> None:
        unsubscribe_disconnect()
        unsubscribe()
        await connection.close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ServerConfigEntry) -> bool:
    entry.runtime_data.unsubscribe_disconnect()
    entry.runtime_data.unsubscribe()
    await entry.runtime_data.connection.close()
    return True
```

Call registration cleanup before closing the transport on unload or shutdown.
Remove the recovery listener first so intentional closure cannot request a reload.
Close the backend on setup failure, including cancellation during registration.
The broad exception handler above performs cleanup and immediately reraises.

LoRaWAN withdraws a failed connection when the backend notifies disconnect
listeners. The server integration schedules its own reload. Authentication errors
start reauthentication; temporary connection errors let HA retry setup.

Keep the same entry ID when reconnecting. `DeviceManager` retains the vendor's
models and coordinators, attaches the replacement transport, and reconciles its
inventory. A disconnect affects only that server. The manager handles removal
when a device or server entry is deleted.

## Supply device identities for discovery

Include the backend's native `stack`, `brand_id`, and `model_id` in each device
descriptor. LoRaWAN uses the stack and brand to discover the vendor integration;
the vendor library uses the model ID to select a device implementation.

When adding another stack, add its identities to the vendor library and its
integration's discovery manifest. Follow
[Dependencies and discovery](/lorawan-connection/home-assistant/device-implementations/#dependencies-and-discovery)
for the manifest and device-library declarations. The `lorawan` discovery field
belongs to the device integration's manifest. The provider supplies the connection.

## Verify the HA boundary

Test config-flow validation, duplicate entries, and reauthentication. Cover setup
success, retryable failure, authentication failure, cancellation, unload, and HA
shutdown. Assert that setup errors close transports and intentional closure does
not schedule recovery.

Connect two servers with the same DevEUI. Check that one can disconnect, recover,
and be deleted without affecting the other. Verify that a reconnect preserves
existing vendor models and coordinators, reconciles inventory, and routes commands
to the replacement connection. Include read-only credentials and device removal.

Finally, exercise the backend against a disposable real server through HA setup,
vendor discovery, an uplink, a TCP outage, and recovery. Test downlinks where the
server and device model support them. Distinguish simulated radio traffic from
physical-device validation in the results.
