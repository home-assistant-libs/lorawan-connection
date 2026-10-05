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
from lorawan_connection.backend.chirpstack import ChirpStackConnection
from lorawan_connection.cli_helper import (
    _watch,
    add_connection_args,
    connect_from_args,
    run,
)
from lorawan_connection.mock import MockConnection
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
        spec=ChirpStackConnection,
        network_id="network",
        tenants=AsyncMock(return_value={"tenant": "Tenant"}),
        applications=AsyncMock(return_value={"app": "App", "app2": "Second"}),
        inventory=AsyncMock(return_value=[DESCRIPTOR]),
        async_subscribe=AsyncMock(),
        async_connect=AsyncMock(),
        error=None,
        close=AsyncMock(),
    )


@pytest.mark.parametrize("backend_args", [(), ("--backend", "chirpstack")])
async def test_scope_autoselection(
    connection: Mock, monkeypatch: pytest.MonkeyPatch, backend_args: tuple[str, ...]
) -> None:
    monkeypatch.setenv("CHIRPSTACK_API_KEY", "secret")
    with patch(
        "lorawan_connection.backend.chirpstack.ChirpStackConnection",
        return_value=connection,
    ) as constructor:
        result = await connect_from_args(args(*backend_args))
    assert result is connection
    assert result.application_ids == ["app", "app2"]
    assert constructor.call_args.args[1] == "secret"
    assert constructor.call_args.kwargs["tenant_id"] is None
    connection.close.assert_not_awaited()


async def test_explicit_scope_and_key_file(connection: Mock, tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("from-file\n")
    with patch(
        "lorawan_connection.backend.chirpstack.ChirpStackConnection",
        return_value=connection,
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
    assert constructor.call_args.kwargs["tenant_id"] == "tenant"
    connection.tenants.assert_not_awaited()


async def test_application_filter_without_tenant(
    connection: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIRPSTACK_API_KEY", "secret")
    with patch(
        "lorawan_connection.backend.chirpstack.ChirpStackConnection",
        return_value=connection,
    ):
        result = await connect_from_args(args("--application", "app2"))
    assert result.application_ids == ["app2"]
    connection.close.assert_not_awaited()


async def test_scoped_key_cannot_list_tenants(
    connection: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIRPSTACK_API_KEY", "secret")
    connection.applications.side_effect = RuntimeError("denied")
    with (
        patch(
            "lorawan_connection.backend.chirpstack.ChirpStackConnection",
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
            "lorawan_connection.backend.chirpstack.ChirpStackConnection",
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
        if name == "backend.chirpstack":
            raise ModuleNotFoundError("chirpstack_api")
        return original_import(name, *args, **kwargs)

    with (
        patch("builtins.__import__", side_effect=no_backend),
        pytest.raises(ValueError, match=r"lorawan-connection\[chirpstack\]"),
    ):
        await connect_from_args(args())


def test_duplicate_model_identity() -> None:
    with pytest.raises(ValueError, match="Duplicate model identity"):
        DeviceCollection(MockConnection(), [S2101, S2101])


async def test_list_supported_models_only(
    connection: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    connection.inventory.return_value = [
        DESCRIPTOR,
        replace(DESCRIPTOR, dev_eui="0000000000000002", model_id="unsupported"),
    ]
    parsed = args("--list", "--json")
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


async def test_invalid_collection_closes_connection(connection: Mock) -> None:
    with (
        patch(
            "lorawan_connection.cli_helper.connect_from_args",
            AsyncMock(return_value=connection),
        ),
        pytest.raises(ValueError, match="Duplicate model identity"),
    ):
        await _watch(args(), [S2101, S2101])
    connection.close.assert_awaited_once()


async def test_live_state_removal_and_disconnect(
    connection: Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    stop = Mock()

    async def subscribe(*, brands, callback):
        now = datetime.now(UTC)
        callback(
            DeviceEventData(
                type=EventType.ADDED, received_at=now, descriptor=DESCRIPTOR
            )
        )
        callback(
            DeviceEventData(
                network_id="network",
                dev_eui=DESCRIPTOR.dev_eui,
                type=EventType.UPLINK,
                received_at=now,
                data=UplinkData(PAYLOAD, 1),
            )
        )
        callback(
            DeviceEventData(
                type=EventType.REMOVED, received_at=now, descriptor=DESCRIPTOR
            )
        )
        connection.error = RuntimeError("offline")
        for registration in tuple(connection.on_disconnect.call_args_list):
            registration.args[0]()
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


@pytest.mark.parametrize("list_only", [False, True])
@pytest.mark.parametrize("json_output", [False, True])
async def test_warn_unmapped_devices_from_supported_vendors(
    connection: Mock,
    capsys: pytest.CaptureFixture[str],
    list_only: bool,
    json_output: bool,
) -> None:
    class OtherVendor(S2101):
        identifiers = {"chirpstack": (123, S2101.identifiers["chirpstack"][1])}

    unmapped = [
        replace(DESCRIPTOR, dev_eui="0000000000000002", model_id="unknown"),
        replace(DESCRIPTOR, dev_eui="0000000000000003", model_id=""),
        replace(
            DESCRIPTOR,
            dev_eui="0000000000000004",
            brand_id=OtherVendor.identifiers["chirpstack"][0],
            model_id="unknown",
        ),
    ]
    descriptors = [
        DESCRIPTOR,
        *unmapped,
        replace(DESCRIPTOR, dev_eui="0000000000000005", brand_id=999),
        replace(DESCRIPTOR, dev_eui="0000000000000006", brand_id=None),
    ]
    connection.inventory.return_value = descriptors

    async def subscribe(*, brands, callback):
        for event_type in (EventType.ADDED, EventType.UPDATED):
            for descriptor in descriptors:
                callback(
                    DeviceEventData(
                        type=event_type,
                        received_at=datetime.now(UTC),
                        descriptor=descriptor,
                    )
                )
        connection.error = RuntimeError("offline")
        for registration in tuple(connection.on_disconnect.call_args_list):
            registration.args[0]()
        return Mock()

    connection.async_subscribe.side_effect = subscribe
    parsed = args()
    parsed.server = "network"
    parsed.list = list_only
    parsed.json = json_output
    with patch(
        "lorawan_connection.cli_helper.connect_from_args",
        AsyncMock(return_value=connection),
    ):
        if list_only:
            await _watch(parsed, [S2101, OtherVendor])
        else:
            with pytest.raises(RuntimeError, match="offline"):
                await _watch(parsed, [S2101, OtherVendor])

    captured = capsys.readouterr()
    warnings = captured.err.splitlines()
    assert len(warnings) == len(unmapped)
    for warning, descriptor in zip(warnings, unmapped, strict=True):
        assert warning.startswith("Warning: unmapped device ")
        assert repr(descriptor.name) in warning
        assert descriptor.dev_eui in warning
        assert f"brand ID {descriptor.brand_id}" in warning
        assert f"model ID {descriptor.model_id or 'missing'}" in warning
    if json_output:
        rows = [json.loads(line) for line in captured.out.splitlines()]
        assert rows
        assert all(row["dev_eui"] == DESCRIPTOR.dev_eui for row in rows)
    else:
        assert f"added: {DESCRIPTOR.name}" in captured.out
        assert "Warning:" not in captured.out
    connection.close.assert_awaited_once()


async def test_cancellation_closes_models_and_connection(connection: Mock) -> None:
    stop = Mock()
    subscribed = asyncio.Event()

    async def subscribe(*, brands, callback):
        callback(
            DeviceEventData(
                type=EventType.ADDED,
                received_at=datetime.now(UTC),
                descriptor=DESCRIPTOR,
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


def test_unknown_backend_does_not_connect(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch("lorawan_connection.cli_helper.connect_from_args") as connect,
        pytest.raises(SystemExit) as result,
    ):
        run(
            SenseCapDeviceCollection.DEVICES,
            ["--backend", "unknown", "--server", "http://localhost:8080"],
        )
    assert result.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
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
    assert "RuntimeError: Bearer <redacted>" in output
    assert "secret" not in output


def test_models_can_share_catalog_ids_across_vendors() -> None:
    class OtherVendor(S2101):
        identifiers = {"chirpstack": (123, S2101.identifiers["chirpstack"][1])}

    collection = DeviceCollection(MockConnection(), [S2101, OtherVendor])
    descriptors = [
        DESCRIPTOR,
        replace(DESCRIPTOR, dev_eui="0000000000000002", brand_id=123),
    ]
    for descriptor in descriptors:
        collection.handle_event(
            DeviceEventData(
                type=EventType.ADDED,
                received_at=datetime.now(UTC),
                descriptor=descriptor,
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
        "lorawan_connection.backend.chirpstack.ChirpStackConnection",
        return_value=connection,
    ):
        await connect_from_args(parsed)
    assert len(threads) == 1
    assert threads[0] != loop_thread


def test_cli_reports_vendor_attributes_and_properties() -> None:
    from lorawan_connection.cli_helper import _model_data

    class MultiChannel(S2101):
        @property
        def channels(self) -> dict[int, dict[str, float]]:
            return {1: {"temperature": 20.0, "humidity": 40.0}}

    model = MultiChannel(DESCRIPTOR)
    assert _model_data(model) == {
        "temperature": None,
        "humidity": None,
        "channels": {1: {"temperature": 20.0, "humidity": 40.0}},
    }


async def test_removed_after_initial_output_failure(
    connection: Mock, capsys, caplog
) -> None:
    """A model property that is not ready cannot break later removal cleanup."""

    class Sensor(S2101):
        @property
        def unobserved(self):
            raise ValueError("No reading yet")

    async def subscribe(*, brands, callback):
        callback(
            DeviceEventData(
                type=EventType.ADDED,
                received_at=datetime.now(UTC),
                descriptor=DESCRIPTOR,
            )
        )
        callback(
            DeviceEventData(
                type=EventType.REMOVED,
                received_at=datetime.now(UTC),
                descriptor=DESCRIPTOR,
            )
        )
        for registration in tuple(connection.on_disconnect.call_args_list):
            registration.args[0]()
        return Mock()

    connection.async_subscribe.side_effect = subscribe
    with (
        patch(
            "lorawan_connection.cli_helper.connect_from_args", return_value=connection
        ),
        pytest.raises(Exception, match="Disconnected"),
    ):
        await _watch(args(), [Sensor])
    assert "removed: Greenhouse" in capsys.readouterr().out
    assert "KeyError" not in caplog.text


async def test_tts_backend_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_API_KEY", "tts-secret")
    connection = Mock()
    with patch(
        "lorawan_connection.backend.tts.TTSConnection", return_value=connection
    ) as constructor:
        result = await connect_from_args(
            args(
                "--backend",
                "tts",
                "--application",
                "my-app",
                "--identity-server",
                "https://identity.example.com:8884",
            )
        )
    assert result is connection
    constructor.assert_called_once_with(
        "http://localhost:8080",
        "tts-secret",
        identity_server="https://identity.example.com:8884",
        application_ids=["my-app"],
        network_id="http://localhost:8080",
    )
