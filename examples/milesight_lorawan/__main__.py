"""Discover Milesight devices and print their state."""

from lorawan_connection.cli_helper import run

from . import MilesightDevices

if __name__ == "__main__":
    run(MilesightDevices.DEVICES)
