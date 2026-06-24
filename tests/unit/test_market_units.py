from __future__ import annotations

from decimal import Decimal

from eflux.market.units import KWH_PER_MWH, internal_cash_to_usd


def test_internal_cash_to_usd_divides_by_kwh_per_mwh():
    # Internal cash is price [$/MWh] x qty [kWh], i.e. 1000x a true-dollar figure.
    assert KWH_PER_MWH == Decimal("1000")
    assert internal_cash_to_usd(Decimal("50")) == Decimal("0.05")
    assert internal_cash_to_usd(Decimal("-1234.5")) == Decimal("-1.2345")
