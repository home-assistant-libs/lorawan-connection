"""The connection interface consumed by device collections."""

from typing import Protocol

from .downlink import Downlink


class Connection(Protocol):
    """Provide network identity and command delivery without owning lifecycle."""

    @property
    def network_id(self) -> str:
        """Identify the logical network."""
        ...

    async def async_send_downlink(self, downlink: Downlink) -> str:
        """Queue a downlink and return its ID for acknowledgement correlation."""
        ...
