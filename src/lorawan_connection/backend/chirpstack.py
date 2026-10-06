"""ChirpStack gRPC inventory plus live events behind one subscription."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

import grpc
from chirpstack_api import api, integration
from google.protobuf.json_format import Parse, ParseError
from google.protobuf.message import Message

from lorawan_connection import (
    AckEvent,
    AddedEvent,
    ConnectionUnavailable,
    DeviceDescriptor,
    DeviceEvent,
    Downlink,
    DownlinkError,
    EventType,
    JoinEvent,
    LocationEvent,
    LogEvent,
    RemovedEvent,
    StatusEvent,
    TxAckEvent,
    Unsubscribe,
    UpdatedEvent,
    UplinkEvent,
    notify,
    subscribe,
)

_LOGGER = logging.getLogger(__name__)
_MESSAGES = {
    "up": integration.UplinkEvent,
    "join": integration.JoinEvent,
    "status": integration.StatusEvent,
    "ack": integration.AckEvent,
    "txack": integration.TxAckEvent,
    "log": integration.LogEvent,
    "location": integration.LocationEvent,
}

_EVENTS = {
    "up": (UplinkEvent, ("data", "f_port")),
    "join": (JoinEvent, ("dev_addr",)),
    "status": (
        StatusEvent,
        (
            "margin",
            "external_power_source",
            "battery_level_unavailable",
            "battery_level",
        ),
    ),
    "ack": (AckEvent, ("queue_item_id", "acknowledged")),
    "txack": (TxAckEvent, ("gateway_id", "downlink_id")),
    "log": (LogEvent, ("description", "level", "code")),
    "location": (LocationEvent, ("latitude", "longitude", "altitude")),
}


class Page[T](Protocol):
    """Read-only list response shape shared by the generated services."""

    @property
    def total_count(self) -> int:
        """Return the total number of matching items."""

    @property
    def result(self) -> Sequence[T]:
        """Return the items in this page."""


class AuthenticationError(Exception):
    """Credentials have been rejected."""


@dataclass(eq=False)
class _Subscriber:
    brands: frozenset[tuple[str, int | str]] | None
    callback: Callable[[DeviceEvent], None]


def connection_error(error: Exception) -> Exception:
    """Classify authentication/access rejection separately from connection loss."""
    if isinstance(error, (ConnectionUnavailable, AuthenticationError)):
        return error
    if isinstance(error, grpc.RpcError):
        message = f"ChirpStack RPC {error.code().name}: {error.details()}"
        failure: Exception = (
            AuthenticationError(message)
            if error.code()
            in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED)
            else ConnectionUnavailable(message)
        )
    else:
        failure = ConnectionUnavailable(f"{type(error).__name__}: {error}")
    failure.__cause__ = error
    return failure


class ChirpStackConnection:
    """Own one channel and one scoped inventory/event subscription.

    Callbacks are synchronous. close() must be awaited by the connection owner.
    A failure marks the connection unavailable before notifying the owner, which
    can create a fresh connection. No replay/history contract is provided.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        tenant_id: str | None = None,
        application_ids: list[str],
        network_id: str,
        poll_interval: float = 30,
        channel: grpc.aio.Channel | None = None,
    ) -> None:
        """Use TLS for https; omit tenant_id to discover all accessible tenants."""
        url = urlsplit(endpoint)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            raise ValueError("Endpoint must be http(s)://host:port")
        target = url.netloc
        self.channel = channel or (
            grpc.aio.secure_channel(target, grpc.ssl_channel_credentials())
            if url.scheme == "https"
            else grpc.aio.insecure_channel(target)
        )
        self.metadata = (("authorization", f"Bearer {api_key}"),)
        self.tenant_id = tenant_id
        self.application_ids = application_ids
        self.network_id = network_id
        self.poll_interval = poll_interval
        self.devices: dict[str, DeviceDescriptor] = {}
        self.available = False
        self.error: Exception | None = None
        self._subscribers: list[_Subscriber] = []
        self._disconnect_listeners: list[Callable[[None], None]] = []
        self._connecting = False
        self._streams: dict[str, asyncio.Task[None]] = {}
        self._poller: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._device_api = api.DeviceServiceStub(self.channel)
        self._profile_api = api.DeviceProfileServiceStub(self.channel)
        self._application_api = api.ApplicationServiceStub(self.channel)
        self._tenant_api = api.TenantServiceStub(self.channel)
        self._internal_api = api.InternalServiceStub(self.channel)

    async def _call[T](
        self, method: Callable[..., Awaitable[T]], request: Message
    ) -> T:
        return await method(request, metadata=self.metadata, timeout=15)

    async def _list[T](
        self,
        method: Callable[..., Awaitable[Page[T]]],
        request_class: type[Message],
        **kwargs: object,
    ) -> list[T]:
        for _ in range(2):
            items: list[T] = []
            total = None
            while True:
                page = await self._call(
                    method, request_class(limit=100, offset=len(items), **kwargs)
                )
                if total is not None and total != page.total_count:
                    break
                total = page.total_count
                items.extend(page.result)
                if len(items) >= page.total_count:
                    return items
                if not page.result:
                    raise ConnectionUnavailable("Incomplete inventory page")
        raise ConnectionUnavailable("Inventory changed during pagination")

    async def tenants(self) -> dict[str, str]:
        """Discover tenants with a global key; scoped keys require a tenant ID."""
        return {
            item.id: item.name
            for item in await self._list(self._tenant_api.List, api.ListTenantsRequest)
        }

    async def applications(self) -> dict[str, str]:
        """List applications in the selected tenant, or all accessible tenants."""
        if self.tenant_id:
            await self._call(
                self._tenant_api.Get, api.GetTenantRequest(id=self.tenant_id)
            )
            tenant_ids = [self.tenant_id]
        else:
            tenant_ids = list(await self.tenants())
        applications = {}
        for tenant_id in tenant_ids:
            items = await self._list(
                self._application_api.List,
                api.ListApplicationsRequest,
                tenant_id=tenant_id,
            )
            applications.update({item.id: item.name for item in items})
        return applications

    async def async_send_downlink(self, downlink: Downlink) -> str:
        """Queue bytes for a selected device, without retrying or awaiting delivery."""
        if not self.available or self._closed:
            raise DownlinkError("LoRaWAN connection is not available")
        if downlink.dev_eui not in self.devices:
            raise DownlinkError("Device is not in the selected inventory")
        try:
            device = (
                await self._call(
                    self._device_api.Get, api.GetDeviceRequest(dev_eui=downlink.dev_eui)
                )
            ).device
            if device.application_id not in self.application_ids:
                raise DownlinkError("Device is no longer in a selected application")
            item = api.DeviceQueueItem(
                dev_eui=downlink.dev_eui,
                f_port=downlink.f_port,
                data=downlink.data,
                confirmed=downlink.confirmed,
            )
            if downlink.expires_at is not None:
                item.expires_at.FromDatetime(downlink.expires_at)
            response: api.EnqueueDeviceQueueItemResponse = await self._call(
                self._device_api.Enqueue,
                api.EnqueueDeviceQueueItemRequest(queue_item=item),
            )
        except grpc.RpcError as error:
            if error.code() in (
                grpc.StatusCode.UNAUTHENTICATED,
                grpc.StatusCode.PERMISSION_DENIED,
            ):
                raise DownlinkError(
                    "ChirpStack rejected the API key or its write permission"
                ) from error
            raise DownlinkError("ChirpStack could not queue the command") from error
        return str(response.id)

    async def inventory(self) -> tuple[DeviceDescriptor, ...]:
        """Read current inventory without starting polls or event streams."""
        return tuple((await self._snapshot()).values())

    async def _snapshot(self) -> dict[str, DeviceDescriptor]:
        applications = await self.applications()
        if not set(self.application_ids) <= applications.keys():
            raise ConnectionUnavailable(
                "Selected application is no longer accessible in the selected tenants"
            )
        profiles = {}
        catalog_devices = {}
        vendors = {}
        snapshot = {}
        for application_id in self.application_ids:
            items = await self._list(
                self._device_api.List,
                api.ListDevicesRequest,
                application_id=application_id,
            )
            for item in items:
                if item.device_profile_id not in profiles:
                    profile = (
                        await self._call(
                            self._profile_api.Get,
                            api.GetDeviceProfileRequest(id=item.device_profile_id),
                        )
                    ).device_profile
                    vendor_id, model, manufacturer = None, "", ""
                    if profile.device_id:
                        try:
                            if profile.device_id not in catalog_devices:
                                catalog_devices[profile.device_id] = (
                                    await self._call(
                                        self._profile_api.GetDevice,
                                        api.GetDeviceProfileDeviceRequest(
                                            id=profile.device_id
                                        ),
                                    )
                                ).device
                            catalog = catalog_devices[profile.device_id]
                            if catalog.vendor_id not in vendors:
                                vendors[catalog.vendor_id] = (
                                    await self._call(
                                        self._profile_api.GetVendor,
                                        api.GetDeviceProfileVendorRequest(
                                            id=catalog.vendor_id
                                        ),
                                    )
                                ).vendor
                            vendor = vendors[catalog.vendor_id]
                            vendor_id, model, manufacturer = (
                                vendor.vendor_id,
                                catalog.name,
                                vendor.name,
                            )
                        except grpc.aio.AioRpcError as error:
                            if error.code() != grpc.StatusCode.NOT_FOUND:
                                raise
                    profiles[item.device_profile_id] = (
                        profile.device_id,
                        vendor_id,
                        model,
                        manufacturer,
                    )
                catalog_id, vendor_id, model, manufacturer = profiles[
                    item.device_profile_id
                ]
                snapshot[item.dev_eui] = DeviceDescriptor(
                    self.network_id,
                    item.dev_eui,
                    item.name,
                    application_id,
                    item.device_profile_id,
                    catalog_id,
                    vendor_id,
                    model,
                    manufacturer,
                    stack="chirpstack",
                )
        return snapshot

    def _emit(
        self, event: DeviceEvent, previous: DeviceDescriptor | None = None
    ) -> None:
        descriptor = event.descriptor or self.devices.get(event.dev_eui)
        if descriptor is None:
            return
        for subscriber in tuple(self._subscribers):
            if subscriber not in self._subscribers:
                continue
            vendors = subscriber.brands
            if vendors is None or (descriptor.stack, descriptor.brand_id) in vendors:
                notify([subscriber.callback], event)
            elif (
                previous is not None and (previous.stack, previous.brand_id) in vendors
            ):
                notify(
                    [subscriber.callback],
                    self._inventory_event(EventType.REMOVED, previous),
                )

    def _inventory_event(
        self, kind: EventType, descriptor: DeviceDescriptor
    ) -> DeviceEvent:
        event_classes: dict[
            EventType, type[AddedEvent | UpdatedEvent | RemovedEvent]
        ] = {
            EventType.ADDED: AddedEvent,
            EventType.UPDATED: UpdatedEvent,
            EventType.REMOVED: RemovedEvent,
        }
        return event_classes[kind](received_at=datetime.now(UTC), descriptor=descriptor)

    async def refresh(self) -> None:
        """Commit complete snapshots without blocking live activity during reads."""
        async with self._lock:
            snapshot = await self._snapshot()
            if self._closed:
                return
            old = self.devices
            self.devices = snapshot
            for eui, descriptor in old.items():
                if eui not in snapshot:
                    if (
                        task := self._streams.pop(eui, None)
                    ) is not None and task is not asyncio.current_task():
                        task.cancel()
                    self._emit(self._inventory_event(EventType.REMOVED, descriptor))
            for eui, descriptor in snapshot.items():
                if eui not in old:
                    self._emit(self._inventory_event(EventType.ADDED, descriptor))
                elif old[eui] != descriptor:
                    self._emit(
                        self._inventory_event(EventType.UPDATED, descriptor), old[eui]
                    )
                if eui not in self._streams:
                    self._streams[eui] = asyncio.create_task(self._stream(eui))

    async def async_connect(self) -> None:
        """Read inventory and start polling and device streams."""
        if self.available or self._connecting or self._closed:
            raise ConnectionUnavailable("Connection is closed or already started")
        self._connecting = True
        try:
            await self.refresh()
        except BaseException as error:
            await self.close()
            if isinstance(error, (grpc.RpcError, ConnectionUnavailable)):
                raise connection_error(error) from error
            raise
        finally:
            self._connecting = False
        if self._closed:
            raise ConnectionUnavailable("Connection closed during startup")
        self.available = True
        self._poller = asyncio.create_task(self._poll())

    async def async_subscribe(
        self,
        *,
        brands: frozenset[tuple[str, int | str]] | None,
        callback: Callable[[DeviceEvent], None],
    ) -> Unsubscribe:
        """Deliver matching inventory, then live events; None selects all vendors."""
        if not self.available or self._closed:
            raise ConnectionUnavailable("Connection is not available")
        subscriber = _Subscriber(brands, callback)
        self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

        for descriptor in tuple(self.devices.values()):
            if subscriber not in self._subscribers:
                break
            if brands is None or (descriptor.stack, descriptor.brand_id) in brands:
                notify([callback], self._inventory_event(EventType.ADDED, descriptor))
        return unsubscribe

    def on_disconnect(self, callback: Callable[[], None]) -> Unsubscribe:
        """Notify after connection loss or closure; callers own recovery."""
        if self._closed:
            raise ConnectionUnavailable("Connection is closed")
        return subscribe(self._disconnect_listeners, lambda _: callback())

    def _notify_disconnect(self) -> None:
        self._subscribers.clear()
        listeners, self._disconnect_listeners = self._disconnect_listeners, []
        notify(listeners, None)
        listeners.clear()

    def _failed(self, error: Exception) -> None:
        if self._closed:
            return
        self.available = False
        self._closed = True
        for task in (*self._streams.values(), self._poller):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        self.error = connection_error(error)
        _LOGGER.warning("ChirpStack connection failed: %s", self.error)
        self._notify_disconnect()

    async def _poll(self) -> None:
        failures = 0
        while not self._closed:
            await asyncio.sleep(self.poll_interval)
            try:
                await self.refresh()
            except (grpc.RpcError, ConnectionUnavailable) as error:
                failures += 1
                if (
                    isinstance(connection_error(error), AuthenticationError)
                    or failures >= 3
                ):
                    self._failed(error)
                    return
                _LOGGER.warning(
                    "ChirpStack inventory refresh failed (%s/3): %s",
                    failures,
                    connection_error(error),
                )
            except Exception as error:
                self._failed(error)
                return
            else:
                failures = 0

    async def _stream(self, dev_eui: str) -> None:
        failures = 0
        while not self._closed and dev_eui in self.devices:
            try:
                async for event in self._read_stream(dev_eui):
                    failures = 0
                    await self.handle_activity(event)
            except (grpc.RpcError, ConnectionUnavailable) as error:
                if (
                    isinstance(error, grpc.RpcError)
                    and error.code() == grpc.StatusCode.NOT_FOUND
                ):
                    try:
                        await self.refresh()
                    except Exception as refresh_error:
                        self._failed(refresh_error)
                        return
                    if dev_eui not in self.devices:
                        return
                failures += 1
                if (
                    isinstance(connection_error(error), AuthenticationError)
                    or failures >= 3
                ):
                    self._failed(error)
                    return
                _LOGGER.warning(
                    "ChirpStack device stream %s failed (%s/3): %s",
                    dev_eui,
                    failures,
                    connection_error(error),
                )
                await asyncio.sleep(1)
            except Exception as error:
                self._failed(error)
                return
            else:
                return

    async def _read_stream(self, dev_eui: str) -> AsyncIterator[DeviceEvent]:
        # Remove this filter when ChirpStack supports disabling retained events.
        cutoff = datetime.now(UTC) - timedelta(seconds=5)
        async for item in self._internal_api.StreamDeviceEvents(
            api.StreamDeviceEventsRequest(dev_eui=dev_eui), metadata=self.metadata
        ):
            message_class = _MESSAGES.get(item.description)
            if message_class is None:
                continue
            # Redis IDs reflect receipt at the server, unlike sensor clocks.
            try:
                recorded_at = datetime.fromtimestamp(
                    int(item.id.split("-", 1)[0]) / 1000, UTC
                )
                if recorded_at < cutoff:
                    continue
                message = Parse(item.body, message_class(), ignore_unknown_fields=True)
            except (ValueError, OverflowError, OSError, ParseError):
                _LOGGER.warning("Ignoring malformed ChirpStack event for %s", dev_eui)
                continue
            if message.device_info.dev_eui.lower() != dev_eui.lower():
                continue
            event_class, fields = _EVENTS[item.description]
            payload = message.location if item.description == "location" else message
            yield event_class(
                network_id=self.network_id,
                dev_eui=dev_eui,
                received_at=recorded_at,
                **{name: getattr(payload, name) for name in fields},
            )
        if dev_eui in self.devices:
            raise ConnectionUnavailable("ChirpStack event stream closed")

    async def handle_activity(self, event: DeviceEvent) -> None:
        """Deliver activity for known devices without waiting on inventory polling."""
        if (
            not self._closed
            and event.network_id == self.network_id
            and event.dev_eui in self.devices
        ):
            self._emit(event)

    async def close(self) -> None:
        """Cancel streams and polls, await cleanup, and close the channel."""
        was_available = self.available
        self.available = False
        self._closed = True
        if was_available:
            self._notify_disconnect()
        self._subscribers.clear()
        self._disconnect_listeners.clear()
        tasks = [
            task
            for task in (*self._streams.values(), self._poller)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._streams.clear()
        await self.channel.close()
