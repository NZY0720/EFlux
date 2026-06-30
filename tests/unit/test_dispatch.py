from decimal import Decimal

from eflux.vpp.dispatch import HeuristicDispatcher


def test_legacy_dispatcher_sells_surplus_with_battery_headroom():
    d = HeuristicDispatcher(price_ref=Decimal("50.0"))

    decision = d.decide(net_kw=2.0, soc_frac=0.5, battery_kw_max=4.0, tick_duration_h=0.25)

    assert decision.side == "sell"
    assert decision.qty_kwh == 0.75
    assert decision.reservation_price == Decimal("35.00")


def test_legacy_dispatcher_buys_deficit_with_battery_room():
    d = HeuristicDispatcher(price_ref=Decimal("50.0"))

    decision = d.decide(net_kw=-2.0, soc_frac=0.25, battery_kw_max=4.0, tick_duration_h=0.25)

    assert decision.side == "buy"
    assert decision.qty_kwh == 0.875
    assert decision.reservation_price == Decimal("65.00")


def test_legacy_dispatcher_noops_when_balanced():
    d = HeuristicDispatcher(price_ref=Decimal("50.0"))

    decision = d.decide(net_kw=0.0, soc_frac=0.5, battery_kw_max=4.0, tick_duration_h=0.25)

    assert decision.side == "none"
    assert decision.qty_kwh == 0.0
    assert decision.reservation_price == Decimal("50.0")
