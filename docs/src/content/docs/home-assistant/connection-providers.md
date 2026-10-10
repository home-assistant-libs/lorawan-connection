---
title: Connection providers
description: Configure a backend, register its connection with LoRaWAN, and own recovery and shutdown in Home Assistant.
---

:::note[Proposal]
The shared `lorawan` integration and registration API described here are proposed
Home Assistant APIs. They are implemented in the PoC, not released in Home Assistant.
:::

A connection provider configures a server backend and registers it with `lorawan`.
[Device integrations](/lorawan-connection/home-assistant/device-implementations/)
use the registered connections through `DeviceManager`.

These examples use `TTSConnection` from the `lorawan-connection` package. To support
another server, implement its adapter using the
[backend guide](/lorawan-connection/connection/adding-a-backend/) first.

## Declare the dependency

A TTS integration needs these manifest fields:

```json
{
  "config_flow": true,
  "dependencies": ["lorawan"],
  "integration_type": "hub",
  "iot_class": "cloud_push",
  "requirements": ["lorawan-connection[tts]==0.13.0"]
}
```

Pin the same `lorawan-connection` version as the shared `lorawan` integration.
For a separately published adapter, use its package name and version in `requirements`.
Import the adapter in the server integration; keep its SDK calls in the backend.

## Configure and validate the server

Collect the endpoint, credentials, and applications or tenant. Derive the config
entry's unique ID from the server and selected scope, excluding credentials.

Validate with a temporary backend connection. This helper returns a config-flow
error key and closes the client:

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

Require a nonempty application selection before calling the helper. Accept read-only
credentials for monitoring. Reauthentication updates and reloads the existing
entry, preserving its ID and scope.

## Connect, register, and close

Subscriptions use `async_subscribe(brands=..., listener=...)`. The initial
inventory must include all devices matching the requested brands.

Set `network_id=entry.entry_id` to distinguish identical DevEUIs on different servers.
The returned unsubscribe callback withdraws the connection; the server integration
owns transport cleanup.

In `__init__.py`, use the config-flow fields and `DOMAIN` from `const.py`:

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

Remove the recovery listener before intentional closure to avoid scheduling a reload.
Then unregister the connection and close the transport. The setup exception handlers
also close the backend if registration fails or is cancelled.

On disconnect, `lorawan` withdraws the connection and the server integration
schedules a reload. `ConfigEntryAuthFailed` starts reauthentication;
`ConfigEntryNotReady` lets HA retry setup. Keep the entry ID on reconnect so
`DeviceManager` can reuse models and coordinators with the replacement transport.

## Supply device identities for discovery

Include the backend's native `stack`, `brand_id`, and `model_id` in each device
descriptor. LoRaWAN uses the stack and brand to discover the vendor integration;
the vendor library uses the model ID to select a device implementation.

Add new stack identities to the vendor library and the device integration's
[discovery manifest](/lorawan-connection/home-assistant/device-implementations/#dependencies-and-discovery).

## Tests

Alongside config-flow and setup tests, cover:

- Setup failure and cancellation closing all transports.
- Unload and HA shutdown without scheduling recovery.
- Two servers with the same DevEUI; disconnecting or deleting one leaves the other available.
- Reconnection preserving models and coordinators, reconciling inventory, and routing commands to the replacement connection.
- Read-only credentials and device removal.

Use a disposable server to test discovery, uplinks, downlinks, and recovery from
a TCP outage. Record whether radio traffic was simulated or came from physical devices.
