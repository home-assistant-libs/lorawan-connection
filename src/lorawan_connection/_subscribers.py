"""Brand-filtered event subscriptions shared by the connections."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from .callbacks import Unsubscribe, notify
from .events import AddedEvent, DeviceDescriptor, DeviceEvent, RemovedEvent


@dataclass(eq=False)
class _Subscriber:
    brands: frozenset[tuple[str, int | str]] | None
    listener: Callable[[DeviceEvent], None]


class Subscribers:
    """Deliver events to brand-filtered listeners with idempotent removal."""

    def __init__(self) -> None:
        self._items: list[_Subscriber] = []

    def add(
        self,
        brands: frozenset[tuple[str, int | str]] | None,
        listener: Callable[[DeviceEvent], None],
        *,
        replay: Iterable[DeviceDescriptor] = (),
    ) -> Unsubscribe:
        """Register one listener, replay matching inventory, and return cleanup."""
        subscriber = _Subscriber(brands, listener)
        self._items.append(subscriber)
        for descriptor in replay:
            if subscriber not in self._items:
                break
            if brands is None or (descriptor.stack, descriptor.brand_id) in brands:
                notify([listener], AddedEvent(descriptor=descriptor))

        def unsubscribe() -> None:
            if subscriber in self._items:
                self._items.remove(subscriber)

        return unsubscribe

    def deliver(
        self,
        event: DeviceEvent,
        descriptor: DeviceDescriptor | None,
        previous: DeviceDescriptor | None = None,
        *,
        removed_at: datetime | None = None,
    ) -> None:
        """Deliver an event to matching listeners, with a removal fallback."""
        if descriptor is None:
            return
        for subscriber in tuple(self._items):
            if subscriber not in self._items:
                continue
            if subscriber.brands is None or (
                (descriptor.stack, descriptor.brand_id) in subscriber.brands
            ):
                notify([subscriber.listener], event)
            elif previous is not None and (
                (previous.stack, previous.brand_id) in subscriber.brands
            ):
                notify(
                    [subscriber.listener],
                    RemovedEvent(
                        received_at=removed_at or datetime.now(UTC),
                        descriptor=previous,
                    ),
                )

    def clear(self) -> None:
        """Drop every listener."""
        self._items.clear()
