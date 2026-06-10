"""Global EventBus singleton — initialized at app startup."""

from __future__ import annotations

from eflux.bridge.bus import EventBus, InMemoryBus

_bus: EventBus | None = None


def set_bus(bus: EventBus) -> None:
    global _bus
    _bus = bus


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = InMemoryBus()
    return _bus
