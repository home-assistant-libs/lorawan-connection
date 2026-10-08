"""Print live SenseCAP readings until disconnected or interrupted."""

import asyncio
import os

from lorawan_connection import ConnectionUnavailable, DeviceCollection
from lorawan_connection.backend.chirpstack import ChirpStackConnection
from sensecap_lorawan import S2101


async def main() -> None:
    connection = ChirpStackConnection(
        endpoint=os.environ["CHIRPSTACK_SERVER"],
        api_key=os.environ["CHIRPSTACK_API_KEY"],
        tenant_id=os.environ.get("CHIRPSTACK_TENANT_ID"),
        application_ids=[],
        network_id="my-network",
    )
    devices = DeviceCollection(connection, [S2101])
    disconnected: asyncio.Future[Exception] = asyncio.get_running_loop().create_future()

    def on_disconnect() -> None:
        if not disconnected.done():
            disconnected.set_result(
                connection.error or ConnectionUnavailable("Disconnected")
            )

    def device_added(device: S2101) -> None:
        print(f"Device: {device.descriptor.name}")
        device.add_update_listener(
            lambda: print(
                f"{device.descriptor.name}: "
                f"temperature={device.temperature}, humidity={device.humidity}"
            )
        )

    devices.subscribe_device_added(device_added)
    try:
        connection.application_ids = list(await connection.applications())
        connection.on_disconnect(on_disconnect)
        await connection.async_connect()
        await devices.async_setup()
        raise await disconnected
    finally:
        devices.close()
        await connection.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
