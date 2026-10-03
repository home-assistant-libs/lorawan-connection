"""Exercise model discovery, output, scope selection, and CLI cleanup."""

import argparse
import asyncio
import builtins
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from lorawan_connection import DeviceCollection, DeviceEventData, EventType, UplinkData
from lorawan_connection.cli_helper import (
    _watch,
    add_connection_args,
    connect_from_args,
    run,
)
from sensecap_lorawan import S2101, SenseCapDeviceCollection

from .test_sensecap_example import DESCRIPTOR, PAYLOAD


def args(*extra: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_connection_args(parser)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(["--server", "http://localhost:8080", *extra])


@pytest.fixture
def connection() -> Mock:
    return Mock(
        network_id="network",
        tenants=AsyncMock(return_value={"tenant": "Tenant"}),
        applications=AsyncMock(return_value={"app": "App", "app2": "Second"}),
        inventory=AsyncMock(return_value=[DESCRIPTOR]),
        async_subscribe=AsyncMock(),
        close=AsyncMock(),
    )


async def test_scope_autoselection(
    connection: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIRPSTACK_API_KEY", "secret")
    with patch(
        "lorawan_connection.chirpstack.ChirpStackConnection", return_value=connection
    ) as constructor:
        result = await connect_from_args(args())
    assert result is connection
    assert result.tenant_id == "tenant"
    assert result.application_ids == ["app", "app2"]
    assert constructor.call_args.args[1] == "secret"
    connection.close.assert_not_awaited()


async def test_explicit_scope_and_key_file(connection: Mock, tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("from-file\n")
    with patch(
        "lorawan_connection.chirpstack.ChirpStackConnection", return_value=connection
    ) as constructor:
        result = await connect_from_args(
            args(
                "--tenant",
                "tenant",
                "--application",
                "app2",
                "--application",
                "app2",
                "--api-key-file",
                str(key),
            )
        )
    assert result.application_ids == ["app2"]
    assert constructor.call_args.args[1] == "from-file"
    connection.tenants.assert_not_awaited()


@pytest.mark.parametrize("tenants", [{}, {"a": "First", "b": "Second"}])
async def test_ambiguous_tenant(
    connection: Mock, monkeypatch: pytest.MonkeyPatch, tenants: dict[str, str]
) -> None:
    monkeypatch.setenv("CHIRPSTACK_API_KEY", "secret")
    connection.tenants.return_value = tenants
    with (
        patch(
            "lorawan_connection.chirpstack.ChirpStackConnection",
            return_value=connection,
        ),
        pytest.raises(ValueError, match="--tenant"),
    ):
        await connect_from_args(args())
    connection.close.assert_awaited_once()


async def test_scoped_key_cannot_list_tenants(
    connection: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIRPSTACK_API_KEY", "secret")
    connection.tenants.side_effect = RuntimeError("denied")
    with (
        patch(
            "lorawan_connection.chirpstack.ChirpStackConnection",
            return_value=connection,
        ),
        pytest.raises(ValueError, match="supply --tenant"),
    ):
        await connect_from_args(args())
    connection.close.assert_awaited_once()


@pytest.mark.parametrize(
    "applications,extra,match",
    [
        ({}, [], "No applications"),
        ({"app": "App"}, ["--application", "wrong"], "does not belong"),
    ],
)
async def test_invalid_applications(
    connection: Mock,
    monkeypatch: pytest.MonkeyPatch,
    applications: dict[str, str],
    extra: list[str],
    match: str,
) -> None:
    monkeypatch.setenv("CHIRPSTACK_API_KEY", "secret")
    connection.applications.return_value = applications
    with (
        patch(
            "lorawan_connection.chirpstack.ChirpStackConnection",
            return_value=connection,
        ),
        pytest.raises(ValueError, match=match),
    ):
        await connect_from_args(args("--tenant", "tenant", *extra))
    connection.close.assert_awaited_once()


async def test_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIRPSTACK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="CHIRPSTACK_API_KEY"):
        await connect_from_args(args())


async def test_missing_optional_backend() -> None:
    original_import = builtins.__import__

    def no_backend(name: str, *args: object, **kwargs: object) -> object:
        if name == "chirpstack":
            raise ModuleNotFoundError("chirpstack_api")
        return original_import(name, *args, **kwargs)

    with (
        patch("builtins.__import__", side_effect=no_backend),
        pytest.raises(ValueError, match=r"lorawan-connection\[chirpstack\]"),
    ):
        await connect_from_args(args())


def test_duplicate_model_identity() -> None:
    with pytest.raises(ValueError, match="Duplicate catalog identity"):
        DeviceCollection([S2101, S2101], network_id="network")


async def test_list_supported_models_only(
    connection: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    connection.inventory.return_value = [
        DESCRIPTOR,
        replace(DESCRIPTOR, dev_eui="0000000000000002", catalog_model_id="unsupported"),
    ]
    parsed = args("--list", "--json")
    parsed.server = "network"
    with patch(
        "lorawan_connection.cli_helper.connect_from_args",
        AsyncMock(return_value=connection),
    ):
        await _watch(parsed, SenseCapDeviceCollection.DEVICES)
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows == [
        {
            "type": "added",
            "dev_eui": DESCRIPTOR.dev_eui,
            "name": DESCRIPTOR.name,
            "model": "S2101",
            "state": {"temperature": None, "humidity": None},
        }
    ]
    connection.async_subscribe.assert_not_awaited()
    connection.close.assert_awaited_once()


async def test_live_state_removal_and_disconnect(
    connection: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    stop = Mock()

    async def subscribe(callback, disconnected):
        now = datetime.now(UTC)
        callback(
            DeviceEventData(
                "network", DESCRIPTOR.dev_eui, EventType.ADDED, now, DESCRIPTOR
            )
        )
        callback(
            DeviceEventData(
                "network",
                DESCRIPTOR.dev_eui,
                EventType.UPLINK,
                now,
                data=UplinkData(PAYLOAD, 1),
            )
        )
        callback(
            DeviceEventData(
                "network", DESCRIPTOR.dev_eui, EventType.REMOVED, now, DESCRIPTOR
            )
        )
        disconnected(RuntimeError("offline"))
        return stop

    connection.async_subscribe.side_effect = subscribe
    parsed = args("--json")
    parsed.server = "network"
    with (
        patch(
            "lorawan_connection.cli_helper.connect_from_args",
            AsyncMock(return_value=connection),
        ),
        pytest.raises(RuntimeError, match="offline"),
    ):
        await _watch(parsed, SenseCapDeviceCollection.DEVICES)
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["type"] for row in rows] == ["added", "state", "removed"]
    assert rows[1]["state"] == {"temperature": 21.4, "humidity": 31.4}
    stop.assert_called_once()
    connection.close.assert_awaited_once()


async def test_cancellation_closes_models_and_connection(connection: Mock) -> None:
    stop = Mock()
    subscribed = asyncio.Event()

    async def subscribe(callback, disconnected):
        callback(
            DeviceEventData(
                "network",
                DESCRIPTOR.dev_eui,
                EventType.ADDED,
                datetime.now(UTC),
                DESCRIPTOR,
            )
        )
        subscribed.set()
        return stop

    connection.async_subscribe.side_effect = subscribe
    parsed = args()
    parsed.server = "network"
    with (
        patch(
            "lorawan_connection.cli_helper.connect_from_args",
            AsyncMock(return_value=connection),
        ),
        patch.object(S2101, "close", autospec=True) as close_model,
    ):
        task = asyncio.create_task(_watch(parsed, SenseCapDeviceCollection.DEVICES))
        await subscribed.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    stop.assert_called_once()
    close_model.assert_called_once()
    connection.close.assert_awaited_once()


def test_help_does_not_connect(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch("lorawan_connection.cli_helper.connect_from_args") as connect,
        pytest.raises(SystemExit) as result,
    ):
        run(SenseCapDeviceCollection.DEVICES, ["--help"])
    assert result.value.code == 0
    assert "--list" in capsys.readouterr().out
    connect.assert_not_called()


def test_connection_error_does_not_print_credentials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch(
            "lorawan_connection.cli_helper.connect_from_args",
            AsyncMock(side_effect=RuntimeError("Bearer secret")),
        ),
        pytest.raises(SystemExit) as result,
    ):
        run(SenseCapDeviceCollection.DEVICES, ["--server", "http://localhost:8080"])
    assert result.value.code == 1
    output = capsys.readouterr().err
    assert "ChirpStack connection failed" in output
    assert "secret" not in output


def test_models_can_share_catalog_ids_across_vendors() -> None:
    class OtherVendor(S2101):
        vendor_id = 123

    collection = DeviceCollection([S2101, OtherVendor], network_id="network")
    descriptors = [
        DESCRIPTOR,
        replace(DESCRIPTOR, dev_eui="0000000000000002", vendor_id=123),
    ]
    for descriptor in descriptors:
        collection.handle_event(
            DeviceEventData(
                "network",
                descriptor.dev_eui,
                EventType.ADDED,
                datetime.now(UTC),
                descriptor,
            )
        )
    assert type(collection.devices[DESCRIPTOR.dev_eui]) is S2101
    assert type(collection.devices["0000000000000002"]) is OtherVendor
    collection.close()


async def test_file_read_runs_off_event_loop(
    connection: Mock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    loop_thread = threading.get_ident()
    threads = []

    def read_key() -> str:
        threads.append(threading.get_ident())
        return "secret"

    parsed = args("--tenant", "tenant")
    parsed.api_key_file = Mock(read_text=read_key)
    with patch(
        "lorawan_connection.chirpstack.ChirpStackConnection", return_value=connection
    ):
        await connect_from_args(parsed)
    assert len(threads) == 1
    assert threads[0] != loop_thread
