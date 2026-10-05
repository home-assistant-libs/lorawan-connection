# ruff: noqa: E402
"""Exercise the spike against a disposable local TTS server, never a public app."""

import argparse
import asyncio
import re
import sys
from pathlib import Path
from uuid import uuid4

sys.path[:0] = [
    str(Path(__file__).parent / "generated"),
    str(Path(__file__).parents[2] / "examples"),
]

import grpc
from backend import TTSConnection
from ttn.lorawan.v3 import application_pb2 as apps
from ttn.lorawan.v3 import application_services_pb2_grpc as apps_grpc
from ttn.lorawan.v3 import applicationserver_pb2 as app
from ttn.lorawan.v3 import applicationserver_pb2_grpc as app_grpc
from ttn.lorawan.v3 import end_device_pb2 as devices
from ttn.lorawan.v3 import end_device_services_pb2_grpc as devices_grpc
from ttn.lorawan.v3 import identifiers_pb2 as ids
from ttn.lorawan.v3 import messages_pb2 as messages
from ttn.lorawan.v3 import networkserver_pb2_grpc as ns_grpc
from ttn.lorawan.v3 import rights_pb2 as rights

from lorawan_connection import Downlink, DownlinkError
from sensecap_lorawan import S2101, SenseCapDeviceCollection


async def until(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(0.02)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-key-file", type=Path, required=True)
    args = parser.parse_args()
    key = re.search(r"NNSXS\.[A-Za-z0-9.]+", args.admin_key_file.read_text())[0]
    metadata = (("authorization", f"Bearer {key}"),)
    channel = grpc.aio.insecure_channel("127.0.0.1:18849")
    app_ids = ids.ApplicationIdentifiers(application_id="spike-" + uuid4().hex[:8])
    applications = apps_grpc.ApplicationRegistryStub(channel)
    registry = devices_grpc.EndDeviceRegistryStub(channel)
    application = app_grpc.AppAsStub(channel)
    await applications.Create(
        apps.CreateApplicationRequest(
            application={"ids": app_ids},
            collaborator={"user_ids": {"user_id": "spike-admin"}},
        ),
        metadata=metadata,
    )
    key_response = await apps_grpc.ApplicationAccessStub(channel).CreateAPIKey(
        apps.CreateApplicationAPIKeyRequest(
            application_ids=app_ids,
            name="spike-consumer",
            rights=[
                rights.RIGHT_APPLICATION_DEVICES_READ,
                rights.RIGHT_APPLICATION_TRAFFIC_READ,
                rights.RIGHT_APPLICATION_TRAFFIC_DOWN_WRITE,
            ],
        ),
        metadata=metadata,
    )
    device_ids = ids.EndDeviceIdentifiers(
        application_ids=app_ids,
        device_id="sensecap-one",
        dev_eui=bytes.fromhex(uuid4().hex[:16]),
        join_eui=bytes(8),
    )
    version = {"brand_id": "sensecap", "model_id": "sensecaps2101-temp-humid"}
    await registry.Create(
        devices.CreateEndDeviceRequest(
            end_device={"ids": device_ids, "name": "Greenhouse", "version_ids": version}
        ),
        metadata=metadata,
    )
    await app_grpc.AsEndDeviceRegistryStub(channel).Set(
        devices.SetEndDeviceRequest(
            end_device={"ids": device_ids, "version_ids": version},
            field_mask={"paths": ["ids", "version_ids"]},
        ),
        metadata=metadata,
    )
    session = {
        "dev_addr": bytes.fromhex("00011BAA"),
        "keys": {
            "session_key_id": b"spike",
            "app_s_key": {"key": bytes.fromhex("0102030405060708090a0b0c0d0e0f10")},
        },
    }
    await app_grpc.AsEndDeviceRegistryStub(channel).Set(
        devices.SetEndDeviceRequest(
            end_device={"ids": device_ids, "session": session},
            field_mask={
                "paths": [
                    "session.dev_addr",
                    "session.keys.session_key_id",
                    "session.keys.app_s_key.key",
                ]
            },
        ),
        metadata=metadata,
    )
    device_ids.dev_addr = session["dev_addr"]
    ns_session = {
        "dev_addr": session["dev_addr"],
        "keys": {
            "f_nwk_s_int_key": {
                "key": bytes.fromhex("0102030405060708090a0b0c0d0e0f10")
            },
            "s_nwk_s_int_key": {
                "key": bytes.fromhex("0102030405060708090a0b0c0d0e0f10")
            },
            "nwk_s_enc_key": {"key": bytes.fromhex("0102030405060708090a0b0c0d0e0f10")},
        },
    }
    ns_result = await ns_grpc.NsEndDeviceRegistryStub(channel).Set(
        devices.SetEndDeviceRequest(
            end_device={
                "ids": device_ids,
                "session": ns_session,
                "frequency_plan_id": "EU_863_870_TTN",
                "lorawan_version": "MAC_V1_0_3",
                "lorawan_phy_version": "PHY_V1_0_3_REV_A",
                "supports_join": False,
            },
            field_mask={
                "paths": [
                    "ids",
                    "session",
                    "frequency_plan_id",
                    "lorawan_version",
                    "lorawan_phy_version",
                    "supports_join",
                ]
            },
        ),
        metadata=metadata,
    )
    session["keys"]["session_key_id"] = ns_result.session.keys.session_key_id
    await app_grpc.AsEndDeviceRegistryStub(channel).Set(
        devices.SetEndDeviceRequest(
            end_device={"ids": device_ids, "session": session},
            field_mask={
                "paths": [
                    "session.dev_addr",
                    "session.keys.session_key_id",
                    "session.keys.app_s_key.key",
                ]
            },
        ),
        metadata=metadata,
    )
    await app_grpc.AsStub(channel).SetLink(
        app.SetApplicationLinkRequest(application_ids=app_ids, link={}),
        metadata=metadata,
    )
    connection = TTSConnection(
        grpc.aio.insecure_channel("127.0.0.1:18849"),
        key_response.key,
        application_ids=[app_ids.application_id],
        network_id="tts-spike",
    )
    models = SenseCapDeviceCollection(connection)
    try:
        await connection.connect()
        await models.async_setup()
        sensor = models.devices[device_ids.dev_eui.hex()]
        assert isinstance(sensor, S2101)
        print("PASS registry list and stack-specific model discovery")
        # Retry simulated frames until the application stream is listening.
        for _ in range(20):
            await application.SimulateUplink(
                messages.ApplicationUp(
                    end_device_ids=device_ids,
                    uplink_message={
                        "f_port": 1,
                        "frm_payload": bytes.fromhex(
                            "01011098530000010210A87A0000AF51"
                        ),
                        "settings": {
                            "frequency": 868100000,
                            "data_rate": {
                                "lora": {
                                    "bandwidth": 125000,
                                    "spreading_factor": 7,
                                    "coding_rate": "4/5",
                                }
                            },
                        },
                    },
                ),
                metadata=metadata,
            )
            await asyncio.sleep(0.1)
            if sensor.temperature is not None:
                break
        assert sensor.temperature == 21.4, (sensor.temperature, connection.error)
        print(
            "PASS AppAs.Subscribe: raw S2101 uplink parsed by the shared model",
            sensor.temperature,
            sensor.humidity,
        )
        command = asyncio.create_task(
            sensor.async_send_downlink(f_port=1, data=b"\x01")
        )
        async with asyncio.timeout(8):
            while True:
                queued = await application.DownlinkQueueList(
                    device_ids, metadata=metadata
                )
                if queued.downlinks:
                    break
                await asyncio.sleep(0.02)
        correlation = next(
            cid
            for cid in queued.downlinks[0].correlation_ids
            if cid.startswith("lorawan-connection:")
        )
        assert queued.downlinks[0].confirmed
        print("PASS confirmed downlink enqueued with caller-generated correlation ID")
        received = []
        await connection.async_subscribe(
            brands=frozenset({("tts", "sensecap")}), callback=received.append
        )
        await application.SimulateUplink(
            messages.ApplicationUp(
                end_device_ids=device_ids,
                downlink_ack={"correlation_ids": [correlation], "confirmed": True},
            ),
            metadata=metadata,
        )
        await until(lambda: any(event.type.value == "ack" for event in received))
        async with asyncio.timeout(8):
            assert await command is None
        print(
            "PASS simulated device ACK completes Device.async_send_downlink "
            "over the real stream"
        )
        read_key = await apps_grpc.ApplicationAccessStub(channel).CreateAPIKey(
            apps.CreateApplicationAPIKeyRequest(
                application_ids=app_ids,
                name="spike-read-only",
                rights=[
                    rights.RIGHT_APPLICATION_DEVICES_READ,
                    rights.RIGHT_APPLICATION_TRAFFIC_READ,
                ],
            ),
            metadata=metadata,
        )
        read_connection = TTSConnection(
            grpc.aio.insecure_channel("127.0.0.1:18849"),
            read_key.key,
            application_ids=[app_ids.application_id],
            network_id="tts-spike",
        )
        try:
            await read_connection.connect()
            try:
                await read_connection.async_send_downlink(
                    Downlink(dev_eui=device_ids.dev_eui.hex(), f_port=1, data=b"\x01")
                )
            except DownlinkError as error:
                assert error.__cause__.code() is grpc.StatusCode.PERMISSION_DENIED
            else:
                raise AssertionError("Read-only key accepted a command")
            assert read_connection.available
            print("PASS read-only key reads devices but cannot enqueue commands")
        finally:
            await read_connection.close()
        late_ids = ids.EndDeviceIdentifiers(
            application_ids=app_ids,
            device_id="late-device",
            dev_eui=bytes.fromhex(uuid4().hex[:16]),
        )
        await registry.Create(
            devices.CreateEndDeviceRequest(
                end_device={
                    "ids": late_ids,
                    "name": "Late device",
                    "version_ids": version,
                }
            ),
            metadata=metadata,
        )
        await until(lambda: late_ids.dev_eui.hex() in models.devices)
        await registry.Delete(late_ids, metadata=metadata)
        await until(lambda: late_ids.dev_eui.hex() not in models.devices)
        print("PASS newly registered device discovered through lifecycle event")
        await registry.Update(
            devices.UpdateEndDeviceRequest(
                end_device={"ids": device_ids, "name": "Renamed"},
                field_mask={"paths": ["name"]},
            ),
            metadata=metadata,
        )
        await until(lambda: sensor.descriptor.name == "Renamed")
        print("PASS lifecycle update event refreshes descriptor without polling")
        await app_grpc.AsEndDeviceRegistryStub(channel).Delete(
            device_ids, metadata=metadata
        )
        await ns_grpc.NsEndDeviceRegistryStub(channel).Delete(
            device_ids, metadata=metadata
        )
        await registry.Delete(device_ids, metadata=metadata)
        await until(lambda: not models.devices)
        print("PASS lifecycle removal retires model without polling")
        print("Observed events:", connection.lifecycle_events)
    finally:
        models.close()
        await connection.close()
        await channel.close()


if __name__ == "__main__":
    asyncio.run(main())
