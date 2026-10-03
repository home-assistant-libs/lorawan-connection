"""Application downlinks and an async transport callback."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime


class DownlinkError(Exception):
    """A command could not be queued or acknowledged."""


@dataclass(frozen=True, slots=True)
class Downlink:
    """Plaintext application bytes for one device; the server handles encryption."""

    dev_eui: str
    f_port: int
    data: bytes
    confirmed: bool = False
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.f_port <= 223:
            raise ValueError("Application FPort must be between 1 and 223")
        if self.expires_at is not None and self.expires_at.utcoffset() is None:
            raise ValueError("Downlink expiry must include a timezone")


type SendDownlink = Callable[[Downlink], Awaitable[str]]
