"""Unit tests for the classical CDA baselines (ZIP / GD / AA).

Each reuses the truthful valuation oracle for its private value (so side/qty match
ZI/Truthful) and layers an adaptive bidding rule on top. The shared invariant — never
quote below marginal cost (sell) or above marginal value (buy) — is enforced by the base
class and checked here for all three; the rest of each test exercises the adaptation.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.aa_agent import AAAgent
from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.gd_agent import GDAgent
from eflux.agents.zip_agent import ZIPAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _make_ctx(
    *,
    pv_kw: float,
    load_kw: float,
    soc_kwh: float = 5.0,
    last_price: float = 50.0,
    best_bid: float = 48.0,
    best_ask: float = 52.0,
    recent_trades: list[dict] | None = None,
    markup_floor: float = 0.4,
) -> AgentContext:
    params = VPPParams(markup_floor=markup_floor)
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=soc_kwh, pv_kw=pv_kw, load_kw=load_kw)
    state.update_net()
    state.pending_net_kwh = state.net_kw * 1.0
    market = MarketSnapshot(
        sim_ts=state.sim_ts,
        best_bid=Decimal(str(best_bid)),
        best_ask=Decimal(str(best_ask)),
        last_price=Decimal(str(last_price)),
        mid_price=Decimal(str((best_bid + best_ask) / 2)),
    )
    market.recent_trades = recent_trades or []
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(kw_peak=params.pv_kw_peak),
        battery=Battery(capacity_kwh=params.battery_kwh, max_power_kw=params.battery_kw_max, soc_kwh=soc_kwh),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=market,
        rng=random.Random(7),
        tick_duration_h=1.0,
    )


# --------------------------------------------------------------------------- shared invariants
def test_all_baselines_side_follows_net_position():
    for agent in (ZIPAgent(), GDAgent(), AAAgent()):
        sell = agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0))
        buy = agent.decide(_make_ctx(pv_kw=0.5, load_kw=3.0))
        assert sell and sell[0].side == "sell", type(agent).__name__
        assert buy and buy[0].side == "buy", type(agent).__name__


def test_all_baselines_balanced_position_no_order():
    for agent in (ZIPAgent(), GDAgent(), AAAgent()):
        assert agent.decide(_make_ctx(pv_kw=2.0, load_kw=2.0)) == [], type(agent).__name__


def test_all_baselines_respect_individual_rationality():
    """A seller never quotes below its marginal cost (fair_sell_price ≈ floor); a buyer never
    above its marginal value (fair_buy_price ≈ price_ref). Sweep market prices to try to push
    each learner past its limit."""
    for AgentCls in (ZIPAgent, GDAgent, AAAgent):
        seller = AgentCls(price_ref=Decimal("50.0"))
        buyer = AgentCls(price_ref=Decimal("50.0"))
        floor = 0.4 * 50.0  # markup_floor * price_ref
        for last in (5.0, 30.0, 50.0, 90.0, 200.0):
            s = seller.decide(_make_ctx(pv_kw=5.0, load_kw=1.0, last_price=last))
            b = buyer.decide(_make_ctx(pv_kw=0.5, load_kw=3.0, last_price=last))
            if s:
                assert float(s[0].price) >= floor - 1e-6, (AgentCls.__name__, last, s[0].price)
            if b:
                assert float(b[0].price) <= 50.0 + 1e-6, (AgentCls.__name__, last, b[0].price)


# --------------------------------------------------------------------------- ZIP
def test_zip_low_market_price_raises_buyer_margin():
    """A buyer seeing the market clear well below its value bids lower over time (margin up)."""
    agent = ZIPAgent(price_ref=Decimal("50.0"))
    prices = []
    for _ in range(15):
        intents = agent.decide(_make_ctx(pv_kw=0.5, load_kw=3.0, last_price=30.0))
        if intents:
            prices.append(float(intents[0].price))
    assert len(prices) >= 3
    assert prices[-1] < prices[0], prices  # bid drifts down toward the cheap market
    assert agent._margin > agent.init_margin


def test_zip_high_market_price_shrinks_buyer_margin_toward_limit():
    """A buyer seeing the market clear at/above its value bids up toward its limit (margin→0)."""
    agent = ZIPAgent(price_ref=Decimal("50.0"))
    last = 0.0
    for _ in range(20):
        intents = agent.decide(_make_ctx(pv_kw=0.5, load_kw=3.0, last_price=60.0))
        if intents:
            last = float(intents[0].price)
    assert last > 47.0, last  # pushed up close to the 50 marginal value
    assert last <= 50.0 + 1e-6


def test_zip_record_trade_captures_own_fill():
    agent = ZIPAgent()
    agent.record_trade({"price": "61.5", "qty": "1", "side": "sell"})
    assert agent._last_fill_price == 61.5


# --------------------------------------------------------------------------- GD
def test_gd_cold_start_quotes_at_limit():
    """With no observed history GD falls back to its limit (truthful)."""
    agent = GDAgent(price_ref=Decimal("50.0"))
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0, last_price=50.0)
    ctx.market.best_bid = None
    ctx.market.best_ask = None
    ctx.market.last_price = None
    ctx.market.mid_price = None
    intents = agent.decide(ctx)
    assert intents and intents[0].side == "buy"
    assert abs(float(intents[0].price) - 50.0) < 1e-6  # == fair_buy_price (limit)


def test_gd_seller_captures_surplus_above_limit_when_bids_are_high():
    """With standing bids well above the seller's floor, GD's belief makes a higher ask
    profitable, so it quotes above its marginal cost rather than dumping at the floor."""
    agent = GDAgent(price_ref=Decimal("50.0"))
    trades = [{"price": 60.0, "qty": 1.0} for _ in range(6)]
    # Warm up the window, then quote.
    price = None
    for _ in range(3):
        intents = agent.decide(
            _make_ctx(pv_kw=5.0, load_kw=1.0, best_bid=60.0, best_ask=62.0, last_price=60.0, recent_trades=trades)
        )
        if intents:
            price = float(intents[0].price)
    floor = 0.4 * 50.0
    assert price is not None
    assert price > floor + 1e-6, price          # captured surplus above the floor
    assert price >= floor - 1e-6                 # still individually rational


# --------------------------------------------------------------------------- AA
def test_aa_tracks_equilibrium_from_recent_trades():
    agent = AAAgent(price_ref=Decimal("50.0"))
    trades = [{"price": 70.0, "qty": 1.0}]
    for _ in range(10):
        agent.decide(_make_ctx(pv_kw=0.5, load_kw=3.0, last_price=70.0, recent_trades=trades))
    assert agent._pstar is not None
    assert 60.0 < agent._pstar <= 70.0, agent._pstar  # EWMA pulled toward the prints


def test_aa_aggressiveness_stays_bounded():
    agent = AAAgent(price_ref=Decimal("50.0"))
    for last in (10.0, 90.0, 20.0, 80.0) * 5:
        agent.decide(_make_ctx(pv_kw=0.5, load_kw=3.0, last_price=last, recent_trades=[{"price": last, "qty": 1.0}]))
        assert -1.0 <= agent._r <= 1.0
