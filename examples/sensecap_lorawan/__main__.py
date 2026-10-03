"""Discover SenseCAP devices and print their state."""

from lorawan_connection.cli_helper import run

from . import SUPPORTED_MODELS

if __name__ == "__main__":
    run(SUPPORTED_MODELS)
