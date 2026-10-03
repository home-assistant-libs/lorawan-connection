"""Listener isolation and independent subscription cleanup."""

from collections.abc import Callable
from unittest.mock import Mock

from lorawan_connection import notify, subscribe


def test_duplicate_subscriptions_have_independent_cleanup() -> None:
    listeners: list[Callable[[int], None]] = []
    callback = Mock()
    stop = subscribe(listeners, callback)
    second = subscribe(listeners, callback)
    notify(listeners, 1)
    assert callback.call_count == 2
    stop()
    stop()
    notify(listeners, 2)
    assert callback.call_count == 3
    second()
    assert not listeners


def test_unsubscribe_and_subscribe_during_delivery() -> None:
    listeners: list[Callable[[int], None]] = []
    late, removed = Mock(), Mock()

    def first(value: int) -> None:
        stop()
        subscribe(listeners, late)

    subscribe(listeners, first)
    stop = subscribe(listeners, removed)
    notify(listeners, 1)
    removed.assert_not_called()
    late.assert_not_called()
    notify(listeners, 2)
    late.assert_called_once_with(2)
