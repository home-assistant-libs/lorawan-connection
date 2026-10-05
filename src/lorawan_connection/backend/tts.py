"""The Things Stack registry, application traffic and lifecycle over gRPC."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import grpc

from lorawan_connection import (
    AckData,
    ConnectionUnavailable,
    CoordinatesData,
    DeviceDescriptor,
    DeviceEvent,
    DeviceEventData,
    Downlink,
    DownlinkError,
    EventType,
    JoinData,
    LocationData,
    Unsubscribe,
    UplinkData,
    notify,
    subscribe,
)

from ._tts_api import Service, message


class AuthenticationError(Exception):
    """The key lacks required device or application-traffic read access."""


def _connection_error(error: Exception) -> Exception:
    if isinstance(error, grpc.RpcError):
        code = error.code()
        if code in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED):
            return AuthenticationError(
                "TTS rejected the API key or its read permissions"
            )
        return ConnectionUnavailable(f"TTS RPC {code.name}")
    return error


def _target(endpoint: str) -> tuple[str, bool]:
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
    # Accessing port also rejects malformed or out-of-range ports.
    port = url.port or (443 if url.scheme == "https" else 80)
    host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
    return f"{host}:{port}", url.scheme == "https"


class TTSConnection:
    """Own independently configured Identity and Application Server connections."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        application_ids: list[str],
        network_id: str,
        identity_server: str | None = None,
        poll_interval: float = 30,
    ) -> None:
        targets = [_target(endpoint), _target(identity_server or endpoint)]
        if not application_ids or any(not app.strip() for app in application_ids):
            raise ValueError("Select at least one TTS application")
        if len(set(application_ids)) != len(application_ids):
            raise ValueError("Application IDs must be unique")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._channels = [
            grpc.aio.secure_channel(target, grpc.ssl_channel_credentials())
            if secure
            else grpc.aio.insecure_channel(target)
            for target, secure in targets
        ]
        self.channel, self._identity_channel = self._channels
        self.metadata = (("authorization", f"Bearer {api_key}"),)
        self.application_ids = list(application_ids)
        self.network_id = network_id
        self.poll_interval = poll_interval
        self.devices: dict[str, DeviceDescriptor] = {}
        self._ids: dict[str, Any] = {}
        self._subscribers: list[
            tuple[
                frozenset[tuple[str, int | str]] | None, Callable[[DeviceEvent], None]
            ]
        ] = []
        self._disconnect: list[Callable[[None], None]] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._closed = False
        self._started = False
        self.available = False
        self.error: Exception | None = None
        self._registry = Service(self._identity_channel, "EndDeviceRegistry")
        self._access = Service(self._identity_channel, "ApplicationAccess")
        self._application = Service(self.channel, "AppAs")
        self._events = Service(self._identity_channel, "Events")

    async def inventory(self) -> list[DeviceDescriptor]:
        """Read a complete inventory and validate the key's traffic-read right."""
        try:
            for app in self.application_ids:
                rights = await self._access.ListRights(
                    message("ApplicationIdentifiers", application_id=app),
                    metadata=self.metadata,
                    timeout=15,
                )
                names = rights.DESCRIPTOR.fields_by_name[
                    "rights"
                ].enum_type.values_by_number
                if "RIGHT_APPLICATION_TRAFFIC_READ" not in {
                    names[value].name for value in rights.rights
                }:
                    raise AuthenticationError(
                        "TTS key needs application traffic read access"
                    )
            await self.refresh()
        except grpc.RpcError as error:
            raise _connection_error(error) from error
        return list(self.devices.values())

    async def async_connect(self) -> None:
        """Read inventory, start streams, then reconcile registrations again."""
        if self._closed or self._started:
            raise ConnectionUnavailable("TTS connection is closed or already started")
        self._started = True
        try:
            async with asyncio.timeout(15):
                await asyncio.gather(
                    *(channel.channel_ready() for channel in self._channels)
                )
            await self.inventory()
            self._tasks.append(asyncio.create_task(self._lifecycle()))
            self._tasks.extend(
                asyncio.create_task(self._traffic(app)) for app in self.application_ids
            )
            async with asyncio.timeout(15):
                await self._ready.wait()
            if self.error is not None:
                raise self.error
            await self.refresh()
            if self.error is not None:
                raise self.error
            self.available = True
            self._tasks.append(asyncio.create_task(self._poll()))
        except BaseException as error:
            await self.close()
            if isinstance(error, grpc.RpcError):
                raise _connection_error(error) from error
            if isinstance(error, TimeoutError):
                raise ConnectionUnavailable("TTS connection timed out") from error
            raise

    def _emit(
        self, event: DeviceEvent, previous: DeviceDescriptor | None = None
    ) -> None:
        descriptor = event.descriptor or self.devices.get(event.dev_eui)
        if descriptor is None:
            return
        for item in tuple(self._subscribers):
            if item not in self._subscribers:
                continue
            brands, callback = item
            if brands is None or (descriptor.stack, descriptor.brand_id) in brands:
                notify([callback], event)
            elif previous and (previous.stack, previous.brand_id) in brands:
                notify([callback], self._event(EventType.REMOVED, previous))

    def _event(self, kind: EventType, descriptor: DeviceDescriptor) -> DeviceEventData:
        return DeviceEventData(
            type=kind, descriptor=descriptor, received_at=datetime.now(UTC)
        )

    async def refresh(self) -> None:
        async with self._lock:
            devices = {}
            identifiers = {}
            for app in self.application_ids:
                page = 1
                while True:
                    result = await self._registry.List(
                        message(
                            "ListEndDevicesRequest",
                            application_ids={"application_id": app},
                            limit=100,
                            page=page,
                            field_mask={"paths": ["name", "version_ids"]},
                        ),
                        metadata=self.metadata,
                        timeout=15,
                    )
                    for device in result.end_devices:
                        if not device.ids.dev_eui:
                            continue
                        eui = device.ids.dev_eui.hex()
                        if eui in devices:
                            raise ConnectionUnavailable(
                                "Duplicate DevEUI in selected applications"
                            )
                        devices[eui] = DeviceDescriptor(
                            network_id=self.network_id,
                            stack="tts",
                            dev_eui=eui,
                            application_id=app,
                            name=device.name or device.ids.device_id,
                            profile_id="",
                            brand_id=device.version_ids.brand_id or None,
                            model_id=device.version_ids.model_id,
                        )
                        identifiers[eui] = device.ids
                    if len(result.end_devices) < 100:
                        break
                    page += 1
            previous, self.devices = self.devices, devices
            self._ids = identifiers
            for eui, descriptor in previous.items():
                if eui not in devices:
                    self._emit(self._event(EventType.REMOVED, descriptor))
            for eui, descriptor in devices.items():
                if eui not in previous:
                    self._emit(self._event(EventType.ADDED, descriptor))
                elif descriptor != previous[eui]:
                    self._emit(
                        self._event(EventType.UPDATED, descriptor), previous[eui]
                    )

    async def _lifecycle(self) -> None:
        try:
            call = self._events.Stream(
                message(
                    "StreamEventsRequest",
                    identifiers=[
                        {"application_ids": {"application_id": app}}
                        for app in self.application_ids
                    ],
                    names=[
                        "end_device.create",
                        "end_device.update",
                        "end_device.delete",
                        "end_device.batch.delete",
                    ],
                ),
                metadata=self.metadata,
            )
            await call.initial_metadata()
            # Events sends an explicit start event after authorization/subscription.
            async for event in call:
                if event.name == "events.stream.start":
                    self._ready.set()
                else:
                    await self.refresh()
            raise ConnectionUnavailable("TTS lifecycle stream ended")
        except Exception as error:
            self._failed(error)

    async def _traffic(self, app: str) -> None:
        try:
            async for up in self._application.Subscribe(
                message("ApplicationIdentifiers", application_id=app),
                metadata=self.metadata,
            ):
                await self.handle_message(up)
            raise ConnectionUnavailable("TTS application stream ended")
        except Exception as error:
            self._failed(error)

    async def _poll(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.poll_interval)
                await self.refresh()
        except Exception as error:
            self._failed(error)

    async def handle_message(self, up: Any) -> None:
        """Translate raw upstream traffic to the shared payload contracts."""
        app = up.end_device_ids.application_ids.application_id
        if app not in self.application_ids:
            return
        eui = up.end_device_ids.dev_eui.hex()
        if not eui:
            return
        if eui not in self.devices:
            await self.refresh()
        if eui not in self.devices:
            return
        if (
            self._ids[eui].device_id != up.end_device_ids.device_id
            or self.devices[eui].application_id != app
        ):
            return
        payload: Any
        if up.HasField("uplink_message"):
            uplink = up.uplink_message
            if uplink.HasField("app_s_key"):
                raise ConnectionUnavailable(
                    "TTS supplied an encrypted application payload"
                )
            kind, payload = (
                EventType.UPLINK,
                UplinkData(bytes(uplink.frm_payload), uplink.f_port),
            )
        elif up.HasField("join_accept"):
            kind, payload = EventType.JOIN, JoinData(up.end_device_ids.dev_addr.hex())
        elif up.HasField("location_solved"):
            if not up.location_solved.HasField("location"):
                return
            location = up.location_solved.location
            kind, payload = (
                EventType.LOCATION,
                LocationData(
                    CoordinatesData(
                        location.latitude,
                        location.longitude,
                        location.altitude,
                    )
                ),
            )
        elif any(
            up.HasField(name)
            for name in ("downlink_ack", "downlink_nack", "downlink_failed")
        ):
            confirmed = up.HasField("downlink_ack")
            downlink = (
                up.downlink_ack
                if confirmed
                else up.downlink_nack
                if up.HasField("downlink_nack")
                else up.downlink_failed.downlink
            )
            correlation = next(
                (
                    cid
                    for cid in downlink.correlation_ids
                    if cid.startswith("lorawan-connection:")
                ),
                None,
            )
            if correlation is None:
                return
            kind, payload = EventType.ACK, AckData(correlation, confirmed)
        else:
            return
        self._emit(
            DeviceEventData(
                type=kind,
                descriptor=self.devices[eui],
                data=payload,
                received_at=up.received_at.ToDatetime(tzinfo=UTC)
                if up.HasField("received_at")
                else datetime.now(UTC),
            )
        )

    async def async_subscribe(
        self,
        *,
        brands: frozenset[tuple[str, int | str]] | None,
        callback: Callable[[DeviceEvent], None],
    ) -> Unsubscribe:
        self._check_available()
        # A unique wrapper makes two subscriptions of the same callback independent.
        item = (brands, lambda event: callback(event))
        self._subscribers.append(item)
        for descriptor in tuple(self.devices.values()):
            if item not in self._subscribers:
                break
            if brands is None or (descriptor.stack, descriptor.brand_id) in brands:
                notify([callback], self._event(EventType.ADDED, descriptor))

        def unsubscribe() -> None:
            if item in self._subscribers:
                self._subscribers.remove(item)

        return unsubscribe

    def on_disconnect(self, callback: Callable[[], None]) -> Unsubscribe:
        if self._closed:
            raise ConnectionUnavailable("TTS connection is closed")
        return subscribe(self._disconnect, lambda _: callback())

    async def async_send_downlink(self, downlink: Downlink) -> str:
        """Queue once and retain a correlation ID for application acknowledgements."""
        if not self.available or self._closed:
            raise DownlinkError("TTS connection is unavailable")
        if downlink.expires_at is not None:
            raise DownlinkError("TTS does not support downlink queue expiry")
        if downlink.dev_eui not in self._ids:
            raise DownlinkError("Device no longer exists")
        correlation = f"lorawan-connection:{uuid4()}"
        try:
            await self._application.DownlinkQueuePush(
                message(
                    "DownlinkQueueRequest",
                    end_device_ids=self._ids[downlink.dev_eui],
                    downlinks=[
                        {
                            "f_port": downlink.f_port,
                            "frm_payload": downlink.data,
                            "confirmed": downlink.confirmed,
                            "correlation_ids": [correlation],
                            "priority": "NORMAL",
                        }
                    ],
                ),
                metadata=self.metadata,
                timeout=15,
            )
        except grpc.RpcError as error:
            raise DownlinkError(
                f"TTS rejected the downlink ({error.code().name})"
            ) from error
        return correlation

    def _check_available(self) -> None:
        if not self.available or self._closed:
            raise ConnectionUnavailable("TTS connection is unavailable")

    def _failed(self, error: Exception) -> None:
        if self._closed or self.error is not None:
            return
        self.available = False
        self.error = _connection_error(error)
        self._ready.set()
        notify(self._disconnect, None)
        for task in self._tasks:
            if task is not asyncio.current_task():
                task.cancel()

    async def close(self) -> None:
        """Release streams and both channels; do not trigger reconnect callbacks."""
        self._closed = True
        self.available = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._subscribers.clear()
        self._disconnect.clear()
        await asyncio.gather(*(channel.close() for channel in self._channels))
