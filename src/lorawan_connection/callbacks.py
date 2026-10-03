"""Synchronous subscriptions for collections and model state."""

import logging
from collections.abc import Callable

type Unsubscribe = Callable[[], None]

_LOGGER = logging.getLogger(__name__)


def notify[T](listeners: list[Callable[[T], None]], value: T) -> None:
    """Call current listeners, isolating failures and allowing unsubscribe."""
    for listener in tuple(listeners):
        if listener not in listeners:
            continue
        try:
            listener(value)
        except Exception:
            _LOGGER.exception("LoRaWAN listener failed")


def subscribe[T](
    listeners: list[Callable[[T], None]], callback: Callable[[T], None]
) -> Unsubscribe:
    """Register one subscription and return its idempotent cleanup function."""

    def listener(value: T) -> None:
        callback(value)

    listeners.append(listener)

    def unsubscribe() -> None:
        if listener in listeners:
            listeners.remove(listener)

    return unsubscribe
