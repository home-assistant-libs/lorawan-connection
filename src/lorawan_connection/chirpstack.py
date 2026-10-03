"""ChirpStack gRPC inventory plus live events behind one subscription."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

import grpc
from chirpstack_api import api, integration
from google.protobuf.json_format import Parse, ParseError
from google.protobuf.message import Message

from lorawan_connection import (
    DeviceDescriptor,
    DeviceEvent,
    DeviceEventData,
    EventType,
    Unsubscribe,
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


class ConnectionUnavailable(Exception):
    """The current subscription cannot deliver events."""


def connection_error(error: Exception) -> Exception:
    """Classify authentication/access rejection separately from connection loss."""
    if (
        isinstance(error, grpc.RpcError)
        and error.code() == grpc.StatusCode.UNAUTHENTICATED
    ):
        return AuthenticationError(
            "ChirpStack rejected the credentials or access scope"
        )
    return ConnectionUnavailable("ChirpStack connection or access failed")


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
        tenant_id: str,
        application_ids: list[str],
        network_id: str,
        *,
        poll_interval: float = 30,
        channel: grpc.aio.Channel | None = None,
    ) -> None:
        """Use TLS for https; never silently downgrade failed TLS."""
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
        self._callback: Callable[[DeviceEvent], None] | None = None
        self._on_disconnect: Callable[[Exception], None] | None = None
        self._streams: dict[str, asyncio.Task[None]] = {}
        self._poller: asyncio.Task[None] | None = None
        self._refresh: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._since = datetime.now(UTC)
        self._pending_unknown = 0
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
        items: list[T] = []
        total = None
        while True:
            page = await self._call(
                method, request_class(limit=100, offset=len(items), **kwargs)
            )
            if total is not None and total != page.total_count:
                raise ConnectionUnavailable("Inventory changed during pagination")
            total = page.total_count
            items.extend(page.result)
            if len(items) >= page.total_count:
                return items
            if not page.result:
                raise ConnectionUnavailable("Incomplete inventory page")

    async def tenants(self) -> dict[str, str]:
        """Discover tenants with a global key; scoped keys require a tenant ID."""
        return {
            item.id: item.name
            for item in await self._list(self._tenant_api.List, api.ListTenantsRequest)
        }

    async def applications(self) -> dict[str, str]:
        """Validate tenant access and list available applications."""
        await self._call(self._tenant_api.Get, api.GetTenantRequest(id=self.tenant_id))
        return {
            item.id: item.name
            for item in await self._list(
                self._application_api.List,
                api.ListApplicationsRequest,
                tenant_id=self.tenant_id,
            )
        }

    async def inventory(self) -> tuple[DeviceDescriptor, ...]:
        """Read current inventory without starting polls or event streams."""
        return tuple((await self._snapshot()).values())

    async def _snapshot(self) -> dict[str, DeviceDescriptor]:
        applications = await self.applications()
        if not set(self.application_ids) <= applications.keys():
            raise ConnectionUnavailable(
                "Selected application no longer belongs to the tenant"
            )
        profiles = {}
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
                            catalog = (
                                await self._call(
                                    self._profile_api.GetDevice,
                                    api.GetDeviceProfileDeviceRequest(
                                        id=profile.device_id
                                    ),
                                )
                            ).device
                            vendor = (
                                await self._call(
                                    self._profile_api.GetVendor,
                                    api.GetDeviceProfileVendorRequest(
                                        id=catalog.vendor_id
                                    ),
                                )
                            ).vendor
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
                )
        return snapshot

    def _emit(self, event: DeviceEvent) -> None:
        if self._callback is not None:
            try:
                self._callback(event)
            except Exception:
                _LOGGER.exception("LoRaWAN event consumer failed")

    def _inventory_event(
        self, kind: EventType, descriptor: DeviceDescriptor
    ) -> DeviceEventData:
        return DeviceEventData(
            self.network_id, descriptor.dev_eui, kind, datetime.now(UTC), descriptor
        )

    async def refresh(self) -> None:
        """Commit only complete snapshots; serialize inventory before activity."""
        async with self._lock:
            snapshot = await self._snapshot()
            if self._closed:
                return
            old = self.devices
            self.devices = snapshot
            for eui, descriptor in old.items():
                if eui not in snapshot:
                    if task := self._streams.pop(eui, None):
                        task.cancel()
                    self._emit(self._inventory_event(EventType.REMOVED, descriptor))
            for eui, descriptor in snapshot.items():
                if eui not in old:
                    self._emit(self._inventory_event(EventType.ADDED, descriptor))
                elif old[eui] != descriptor:
                    self._emit(self._inventory_event(EventType.UPDATED, descriptor))
                if eui not in self._streams:
                    self._streams[eui] = asyncio.create_task(self._stream(eui))

    async def async_subscribe(
        self,
        callback: Callable[[DeviceEvent], None],
        on_disconnect: Callable[[Exception], None],
    ) -> Unsubscribe:
        """Deliver current inventory before returning the unsubscribe function."""
        if self.available or self._callback is not None or self._closed:
            raise ConnectionUnavailable("Connection is closed or already subscribed")
        self._callback, self._on_disconnect = callback, on_disconnect
        try:
            await self.refresh()
        except BaseException as error:
            await self.close()
            if isinstance(error, (grpc.RpcError, ConnectionUnavailable)):
                raise connection_error(error) from error
            raise
        self.available = True
        self._poller = asyncio.create_task(self._poll())

        def unsubscribe() -> None:
            self._callback = None
            self._on_disconnect = None

        return unsubscribe

    def _failed(self, error: Exception) -> None:
        if self._closed:
            return
        self.available = False
        self._closed = True
        for task in (*self._streams.values(), self._poller):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        if self._on_disconnect:
            self._on_disconnect(connection_error(error))

    async def _poll(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.poll_interval)
                await self.refresh()
        except (grpc.RpcError, ConnectionUnavailable) as error:
            self._failed(error)

    async def _stream(self, dev_eui: str) -> None:
        try:
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
                    if recorded_at < self._since:
                        continue
                    message = Parse(
                        item.body, message_class(), ignore_unknown_fields=True
                    )
                except (ValueError, ParseError):
                    _LOGGER.warning(
                        "Ignoring malformed ChirpStack event for %s", dev_eui
                    )
                    continue
                if message.device_info.dev_eui.lower() != dev_eui.lower():
                    continue
                await self.handle_activity(
                    DeviceEventData(
                        self.network_id,
                        dev_eui,
                        EventType(item.description),
                        recorded_at,
                        data=message,
                    )
                )
            if dev_eui in self.devices:
                self._failed(ConnectionUnavailable("ChirpStack event stream closed"))
        except grpc.aio.AioRpcError as error:
            if error.code() == grpc.StatusCode.NOT_FOUND:
                try:
                    await self.refresh()
                except (grpc.RpcError, ConnectionUnavailable) as refresh_error:
                    self._failed(refresh_error)
                else:
                    if dev_eui in self.devices:
                        self._failed(ConnectionUnavailable("Device stream disappeared"))
                return
            self._failed(error)
        except ConnectionUnavailable as error:
            self._failed(error)

    async def handle_activity(self, event: DeviceEvent) -> None:
        """Refresh unknown devices before delivery, coalescing concurrent misses."""
        if self._closed or event.network_id != self.network_id:
            return
        if event.dev_eui not in self.devices:
            if self._pending_unknown >= 64:
                return
            if self._refresh is None or self._refresh.done():
                self._refresh = asyncio.create_task(self.refresh())
            self._pending_unknown += 1
            try:
                await asyncio.shield(self._refresh)
            finally:
                self._pending_unknown -= 1
        async with self._lock:
            if event.dev_eui in self.devices and not self._closed:
                self._emit(event)

    async def close(self) -> None:
        """Cancel streams and polls, await cleanup, and close the channel."""
        self.available = False
        self._closed = True
        self._callback = self._on_disconnect = None
        tasks = [
            task
            for task in (*self._streams.values(), self._poller, self._refresh)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._streams.clear()
        await self.channel.close()
