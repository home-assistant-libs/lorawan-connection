"""Run a device library against the selected optional server backend."""

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from . import (
    Connection,
    ConnectionUnavailable,
    Device,
    DeviceCollection,
    DeviceDescriptor,
    DeviceEventData,
    EventType,
)
from .callbacks import Unsubscribe

if TYPE_CHECKING:
    from .backend.chirpstack import ChirpStackConnection
    from .backend.tts import TTSConnection


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    """Add server, key, tenant, and application selection arguments."""
    parser.add_argument(
        "--backend",
        choices=("chirpstack", "tts"),
        default="chirpstack",
        help="Server backend (default: chirpstack)",
    )
    parser.add_argument(
        "--server", required=True, help="Application server gRPC http(s) URL"
    )
    parser.add_argument("--identity-server", help="TTS Identity Server gRPC URL")
    parser.add_argument(
        "--api-key-file", type=Path, help="Read the API key from a file"
    )
    parser.add_argument(
        "--tenant", help="Tenant UUID; defaults to all accessible tenants"
    )
    parser.add_argument(
        "--application",
        action="append",
        default=[],
        help="Application UUID; repeatable",
    )


async def connect_from_args(
    args: argparse.Namespace,
) -> "ChirpStackConnection | TTSConnection":
    """Open the selected backend with its environment key or an explicit key file."""
    if args.backend == "tts":
        try:
            from .backend.tts import TTSConnection
        except ModuleNotFoundError as error:
            raise ValueError(
                'Install the backend: pip install "lorawan-connection[tts]"'
            ) from error
        if args.tenant:
            raise ValueError("--tenant is only supported by ChirpStack")
        key = (
            (await asyncio.to_thread(args.api_key_file.read_text)).strip()
            if args.api_key_file
            else os.environ.get("TTS_API_KEY", "").strip()
        )
        if not key:
            raise ValueError("Set TTS_API_KEY or use --api-key-file")
        return TTSConnection(
            args.server,
            key,
            identity_server=args.identity_server,
            application_ids=list(dict.fromkeys(args.application)),
            network_id=args.server,
        )
    if args.backend != "chirpstack":
        raise ValueError(f"Unsupported backend: {args.backend}")
    if args.identity_server:
        raise ValueError("--identity-server is only supported by TTS")
    try:
        from .backend.chirpstack import ChirpStackConnection
    except ModuleNotFoundError as error:
        raise ValueError(
            'Install the backend: pip install "lorawan-connection[chirpstack]"'
        ) from error

    key = (
        (await asyncio.to_thread(args.api_key_file.read_text)).strip()
        if args.api_key_file
        else os.environ.get("CHIRPSTACK_API_KEY", "").strip()
    )
    if not key:
        raise ValueError("Set CHIRPSTACK_API_KEY or use --api-key-file")
    connection = ChirpStackConnection(
        args.server,
        key,
        tenant_id=args.tenant,
        application_ids=[],
        network_id=args.server,
    )
    try:
        try:
            applications = await connection.applications()
        except Exception as error:
            if args.tenant:
                raise
            raise ValueError(
                "Cannot discover applications; check the endpoint and API key. "
                "If the key cannot list tenants, supply --tenant with its UUID"
            ) from error
        selected = args.application or list(applications)
        if not selected:
            raise ValueError("No applications are available in the selected tenants")
        if not set(selected) <= applications.keys():
            raise ValueError(
                "Selected application does not belong to the selected tenants"
            )
        connection.application_ids = list(dict.fromkeys(selected))
        return connection
    except BaseException:
        await connection.close()
        raise


def _json_default(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _model_data(device: Device) -> dict[str, object]:
    """Read public model attributes and properties for diagnostic output."""
    return {
        name: value
        for name in dir(device)
        if not name.startswith("_")
        and name not in {"descriptor", "identifiers", "closed"}
        and not callable(value := getattr(device, name))
    }


class _CLICollection(DeviceCollection[Device]):
    """Report unmapped devices from vendors represented by the supplied models."""

    def __init__(self, connection: Connection, models: Sequence[type[Device]]) -> None:
        super().__init__(connection, models)
        self._brands = {(stack, brand) for stack, brand, _ in self._models}
        self._warned: set[str] = set()

    def _create_device(self, descriptor: DeviceDescriptor) -> Device | None:
        device = super()._create_device(descriptor)
        if (
            device is None
            and (descriptor.stack, descriptor.brand_id) in self._brands
            and descriptor.dev_eui not in self._warned
        ):
            self._warned.add(descriptor.dev_eui)
            print(
                f"Warning: unmapped device {descriptor.name!r} "
                f"(DevEUI {descriptor.dev_eui}, stack {descriptor.stack}, "
                f"brand ID {descriptor.brand_id}, "
                f"model ID {descriptor.model_id or 'missing'}). "
                "No supported model matches this device.",
                file=sys.stderr,
            )
        return device


async def _watch(args: argparse.Namespace, models: Sequence[type[Device]]) -> None:
    connection = await connect_from_args(args)
    collection = None
    subscriptions: dict[str, Unsubscribe] = {}
    finished: asyncio.Future[Exception] = asyncio.get_running_loop().create_future()

    def on_disconnect() -> None:
        if not finished.done():
            finished.set_result(
                connection.error or ConnectionUnavailable("Disconnected")
            )

    def output(kind: str, device: Device, state: object = None) -> None:
        descriptor = device.descriptor
        if args.json:
            line = json.dumps(
                {
                    "type": kind,
                    "dev_eui": descriptor.dev_eui,
                    "name": descriptor.name,
                    "model": type(device).__name__,
                    "state": state,
                },
                default=_json_default,
            )
        else:
            suffix = f" {state}" if state is not None else ""
            line = (
                f"{kind}: {descriptor.name} "
                f"({descriptor.dev_eui}, {type(device).__name__}){suffix}"
            )
        print(line, flush=True)

    def added(device: Device) -> None:
        subscriptions[device.descriptor.dev_eui] = device.add_update_listener(
            lambda: output("state", device, _model_data(device))
        )
        output("added", device, _model_data(device))

    def removed(device: Device) -> None:
        if unsubscribe := subscriptions.pop(device.descriptor.dev_eui, None):
            unsubscribe()
        output("removed", device)

    try:
        collection = _CLICollection(connection, models)
        collection.subscribe_device_added(added)
        unsubscribe_removed = collection.subscribe_device_removed(removed)
        if args.list:
            for descriptor in await connection.inventory():
                collection.handle_event(
                    DeviceEventData(
                        type=EventType.ADDED,
                        received_at=datetime.now(UTC),
                        descriptor=descriptor,
                    )
                )
            return
        connection.on_disconnect(on_disconnect)
        await connection.async_connect()
        await collection.async_setup()
        raise await finished
    finally:
        for stop_model in subscriptions.values():
            stop_model()
        if collection is not None:
            unsubscribe_removed()
            collection.close()
        await connection.close()


def run(models: Sequence[type[Device]], argv: Sequence[str] | None = None) -> None:
    """Discover supported devices and print state until interrupted or disconnected."""
    parser = argparse.ArgumentParser(
        description="Discover devices and watch their state."
    )
    add_connection_args(parser)
    parser.add_argument(
        "--list", action="store_true", help="List supported devices and exit"
    )
    parser.add_argument(
        "--json", action="store_true", help="Print newline-delimited JSON"
    )
    args = parser.parse_args(argv)
    try:
        asyncio.run(_watch(args, models))
    except KeyboardInterrupt:
        return
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    except Exception as error:
        message = re.sub(r"(?i)Bearer\s+\S+", "Bearer <redacted>", str(error))
        parser.exit(1, f"{type(error).__name__}: {message}\n")
