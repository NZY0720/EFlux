"""Public market V2 surface, loaded lazily to keep submodules acyclic."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "DecisionRound": ("eflux.market.scheduler", "DecisionRound"),
    "DeliveryInterval": ("eflux.market.products", "DeliveryInterval"),
    "DeliveryPosition": ("eflux.market.delivery", "DeliveryPosition"),
    "EconomicLedger": ("eflux.market.ledger", "EconomicLedger"),
    "FairDecisionScheduler": ("eflux.market.scheduler", "FairDecisionScheduler"),
    "LedgerCategory": ("eflux.market.ledger", "LedgerCategory"),
    "OrderPurpose": ("eflux.market.delivery", "OrderPurpose"),
    "ProductMatchingEngine": ("eflux.market.product_engine", "ProductMatchingEngine"),
    "SettlementPrices": ("eflux.market.settlement", "SettlementPrices"),
    "SettlementResult": ("eflux.market.settlement", "SettlementResult"),
    "TimeInForce": ("eflux.market.products", "TimeInForce"),
    "TradingGatewayV2": ("eflux.market.gateway", "TradingGatewayV2"),
    "delivery_horizon": ("eflux.market.products", "delivery_horizon"),
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
