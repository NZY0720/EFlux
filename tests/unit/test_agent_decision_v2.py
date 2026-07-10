from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from eflux.agents.decision import AgentDecision, CancelRequest, OrderRequest, ReplaceRequest
from eflux.market.delivery import OrderPurpose
from eflux.market.products import DeliveryInterval, TimeInForce


def _interval() -> DeliveryInterval:
    start = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
    return DeliveryInterval(
        "p2p", start, start + timedelta(minutes=5), start, start - timedelta(minutes=30)
    )


def _order(**overrides) -> OrderRequest:
    values = {
        "side": "sell",
        "price": Decimal("50"),
        "qty_kwh": Decimal("0.1"),
        "interval": _interval(),
        "purpose": OrderPurpose.BALANCE,
        "time_in_force": TimeInForce.GOOD_TIL_GATE,
    }
    values.update(overrides)
    return OrderRequest(**values)


def test_decision_carries_orders_cancels_and_atomic_replacements():
    replacement = _order(price=Decimal("49"))
    decision = AgentDecision(
        orders=(_order(),),
        cancels=(CancelRequest(1),),
        replaces=(ReplaceRequest(2, replacement),),
    )
    assert not decision.is_empty


def test_signed_prices_are_valid_but_quantity_must_be_positive():
    assert _order(price=Decimal("-50")).price == Decimal("-50")
    with pytest.raises(ValueError, match="qty_kwh"):
        _order(qty_kwh=Decimal("0"))


def test_physical_purpose_constrains_order_direction():
    with pytest.raises(ValueError, match="dispatchable"):
        _order(side="buy", purpose=OrderPurpose.DISPATCHABLE)
    with pytest.raises(ValueError, match="flexible load"):
        _order(side="sell", purpose=OrderPurpose.FLEX_LOAD)


def test_hold_is_an_explicit_empty_decision():
    decision = AgentDecision.hold("forecast uncertainty")
    assert decision.is_empty
    assert decision.rationale == "forecast uncertainty"
