"""Experimental TTS gRPC adapter; generated modules are built by generate.py."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import grpc
from ttn.lorawan.v3 import (
    applicationserver_pb2_grpc as app_grpc,
)
from ttn.lorawan.v3 import (
    end_device_pb2 as device_pb,
)
from ttn.lorawan.v3 import (
    end_device_services_pb2_grpc as device_grpc,
)
from ttn.lorawan.v3 import (
    events_pb2 as events_pb,
)
from ttn.lorawan.v3 import (
    events_pb2_grpc as events_grpc,
)
from ttn.lorawan.v3 import (
    identifiers_pb2 as ids_pb,
)
from ttn.lorawan.v3 import (
    messages_pb2 as messages_pb,
)

from lorawan_connection import (
    AckData,
    ConnectionUnavailable,
    DeviceDescriptor,
    DeviceEvent,
    DeviceEventData,
    Downlink,
    DownlinkError,
    EventType,
    Unsubscribe,
    UplinkData,
    notify,
    subscribe,
)


class TTSConnection:
    """Combine the registry, application streams and lifecycle events."""

    def __init__(
        self,
        channel: grpc.aio.Channel,
        api_key: str,
        *,
        application_ids: list[str],
        network_id: str,
    ) -> None:
        self.channel = channel
        self.metadata = (("authorization", f"Bearer {api_key}"),)
        self.application_ids = application_ids
        self.network_id = network_id
        self.devices: dict[str, DeviceDescriptor] = {}
        self._ids: dict[str, ids_pb.EndDeviceIdentifiers] = {}
        self._subscribers: list[
            tuple[
                frozenset[tuple[str, int | str]] | None, Callable[[DeviceEvent], None]
            ]
        ] = []
        self._disconnect: list[Callable[[None], None]] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()
        self.available = False
        self.error: Exception | None = None
        self.lifecycle_events: list[str] = []
        self._registry = device_grpc.EndDeviceRegistryStub(channel)
        self._application = app_grpc.AppAsStub(channel)
        self._events = events_grpc.EventsStub(channel)

    async def connect(self) -> None:
        await self.refresh()
        self.available = True
        self._tasks = [asyncio.create_task(self._lifecycle())]
        self._tasks.extend(
            asyncio.create_task(self._traffic(app)) for app in self.application_ids
        )

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
                        device_pb.ListEndDevicesRequest(
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
                events_pb.StreamEventsRequest(
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
            async for event in call:
                self.lifecycle_events.append(event.name)
                await self.refresh()
            raise ConnectionUnavailable("TTS lifecycle stream ended")
        except Exception as error:
            self._failed(error)

    async def _traffic(self, app: str) -> None:
        try:
            async for message in self._application.Subscribe(
                ids_pb.ApplicationIdentifiers(application_id=app),
                metadata=self.metadata,
            ):
                await self.handle_message(message)
            raise ConnectionUnavailable("TTS application stream ended")
        except Exception as error:
            self._failed(error)

    async def handle_message(self, message: messages_pb.ApplicationUp) -> None:
        eui = message.end_device_ids.dev_eui.hex()
        if eui not in self.devices:
            await self.refresh()
        if eui not in self.devices:
            return
        if message.HasField("uplink_message"):
            uplink = message.uplink_message
            # A deployment that skips payload crypto cannot feed plaintext models.
            if uplink.HasField("app_s_key"):
                raise ConnectionUnavailable(
                    "TTS supplied an encrypted application payload"
                )
            kind, payload = (
                EventType.UPLINK,
                UplinkData(bytes(uplink.frm_payload), uplink.f_port),
            )
        elif message.HasField("downlink_ack") or message.HasField("downlink_nack"):
            confirmed = message.HasField("downlink_ack")
            downlink = message.downlink_ack if confirmed else message.downlink_nack
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
                received_at=message.received_at.ToDatetime(tzinfo=UTC),
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
        self._check_available()
        return subscribe(self._disconnect, lambda _: callback())

    async def async_send_downlink(self, downlink: Downlink) -> str:
        self._check_available()
        if downlink.expires_at is not None:
            raise DownlinkError("TTS spike does not support queue expiry")
        if downlink.dev_eui not in self._ids:
            raise DownlinkError("Device no longer exists")
        correlation = f"lorawan-connection:{uuid4()}"
        try:
            await self._application.DownlinkQueuePush(
                messages_pb.DownlinkQueueRequest(
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
            raise DownlinkError("TTS rejected the downlink") from error
        return correlation

    def _check_available(self) -> None:
        if not self.available:
            raise ConnectionUnavailable("TTS connection is unavailable")

    def _failed(self, error: Exception) -> None:
        if not self.available:
            return
        self.available = False
        self.error = error
        notify(self._disconnect, None)
        for task in self._tasks:
            if task is not asyncio.current_task():
                task.cancel()

    async def close(self) -> None:
        self.available = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._subscribers.clear()
        self._disconnect.clear()
        await self.channel.close()
