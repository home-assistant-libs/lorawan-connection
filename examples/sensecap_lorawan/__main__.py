"""Discover SenseCAP devices and print their state."""

from lorawan_connection.cli_helper import run

from . import SenseCapDeviceCollection

if __name__ == "__main__":
    run(SenseCapDeviceCollection.DEVICES)
