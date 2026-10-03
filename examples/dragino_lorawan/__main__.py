"""Discover Dragino devices and print their relay states."""

from lorawan_connection.cli_helper import run

from . import DraginoDevices

if __name__ == "__main__":
    run(DraginoDevices.DEVICES)
