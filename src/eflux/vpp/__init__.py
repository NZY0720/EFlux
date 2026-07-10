"""Public VPP surface, loaded lazily to keep physical submodules acyclic."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "PV": ("eflux.vpp.der", "PV"),
    "BalanceReservationBook": ("eflux.vpp.reservations", "BalanceReservationBook"),
    "Battery": ("eflux.vpp.der", "Battery"),
    "BatteryReservationBook": ("eflux.vpp.reservations", "BatteryReservationBook"),
    "DispatchableReservationBook": (
        "eflux.vpp.reservations",
        "DispatchableReservationBook",
    ),
    "FlexibleLoad": ("eflux.vpp.der", "FlexibleLoad"),
    "FlexibleLoadReservationBook": (
        "eflux.vpp.reservations",
        "FlexibleLoadReservationBook",
    ),
    "HeuristicDispatcher": ("eflux.vpp.dispatch", "HeuristicDispatcher"),
    "VPPParams": ("eflux.vpp.base", "VPPParams"),
    "VPPState": ("eflux.vpp.base", "VPPState"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
