"""Run a device library against ChirpStack with its supported model classes."""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from . import Device, DeviceCollection, DeviceEventData, EventType
from .callbacks import Unsubscribe

if TYPE_CHECKING:
    from .chirpstack import ChirpStackConnection


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    """Add server, key, tenant, and application selection arguments."""
    parser.add_argument("--server", required=True, help="ChirpStack gRPC http(s) URL")
    parser.add_argument(
        "--api-key-file", type=Path, help="Read the API key from a file"
    )
    parser.add_argument(
        "--tenant", help="Tenant UUID; auto-selected if only one exists"
    )
    parser.add_argument(
        "--application",
        action="append",
        default=[],
        help="Application UUID; repeatable",
    )


async def connect_from_args(args: argparse.Namespace) -> "ChirpStackConnection":
    """Open a scoped connection; use CHIRPSTACK_API_KEY unless a file is supplied."""
    try:
        from .chirpstack import ChirpStackConnection
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
        args.server, key, args.tenant or "", [], args.server
    )
    try:
        if not args.tenant:
            try:
                tenants = await connection.tenants()
            except Exception as error:
                raise ValueError(
                    "Cannot list tenants; supply --tenant with its UUID"
                ) from error
            if len(tenants) != 1:
                choices = ", ".join(
                    f"{name} ({uuid})" for uuid, name in tenants.items()
                )
                raise ValueError(
                    f"Select a tenant with --tenant. Available: {choices or 'none'}"
                )
            connection.tenant_id = next(iter(tenants))
        applications = await connection.applications()
        selected = args.application or list(applications)
        if not selected:
            raise ValueError("No applications are available in this tenant")
        if not set(selected) <= applications.keys():
            raise ValueError("Selected application does not belong to this tenant")
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
        and name not in {"descriptor", "vendor_id", "catalog_model_id", "closed"}
        and not callable(value := getattr(device, name))
    }


async def _watch(args: argparse.Namespace, models: Sequence[type[Device]]) -> None:
    collection = DeviceCollection[Device](models, network_id=args.server)
    subscriptions: dict[str, Unsubscribe] = {}
    finished: asyncio.Future[Exception] = asyncio.get_running_loop().create_future()

    def on_disconnect(error: Exception) -> None:
        if not finished.done():
            finished.set_result(error)

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
        output("added", device, _model_data(device))
        subscriptions[device.descriptor.dev_eui] = device.add_update_listener(
            lambda: output("state", device, _model_data(device))
        )

    def removed(device: Device) -> None:
        subscriptions.pop(device.descriptor.dev_eui)()
        output("removed", device)

    collection.subscribe_device_added(added)
    stop_removed = collection.subscribe_device_removed(removed)
    connection = None
    try:
        connection = await connect_from_args(args)
        if args.list:
            for descriptor in await connection.inventory():
                collection.handle_event(
                    DeviceEventData(
                        connection.network_id,
                        descriptor.dev_eui,
                        EventType.ADDED,
                        datetime.now(UTC),
                        descriptor,
                    )
                )
            return
        stop = await connection.async_subscribe(collection.handle_event, on_disconnect)
        try:
            error = await finished
        finally:
            stop()
        raise error
    finally:
        stop_removed()
        for stop_model in subscriptions.values():
            stop_model()
        collection.close()
        if connection is not None:
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
    except Exception:
        # gRPC details may contain server data; keep credentials out of diagnostics.
        print(
            "ChirpStack connection failed. Check the endpoint, key, and access scope.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
