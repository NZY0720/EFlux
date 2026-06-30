"""Backtest-only defaults and roster selection."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from eflux.agents.base import MarketSnapshot
from eflux.agents.reflective.strategist import LLMStrategist
from eflux.backtest import (
    BacktestConfig,
    BacktestError,
    default_scenario_for_market,
    inspect_scenario,
    resolve_scenario_path,
)
from eflux.backtest.runner import (
    _FALLBACK_PRICE,
    _historical_quote,
    _real_price_points,
    _refresh_llm_fleet,
    _validate_live_strict_llm,
)
from eflux.config import PROJECT_ROOT


def test_backtest_defaults_are_one_month_one_second_hourly_strict_llm():
    cfg = BacktestConfig()
    assert cfg.months == 1
    assert cfg.tick_seconds == 1.0
    assert cfg.llm_cadence_hours == 1.0
    assert cfg.llm_mode == "live-strict"


def test_backtest_uses_latest_market_rosters_not_legacy_default():
    p2p = resolve_scenario_path("p2p")
    realprice = resolve_scenario_path("realprice")
    assert p2p == PROJECT_ROOT / "scenarios" / "p2p.yaml"
    assert realprice == PROJECT_ROOT / "scenarios" / "realprice.yaml"
    assert default_scenario_for_market("p2p").name != "default.yaml"
    assert default_scenario_for_market("realprice").name != "default.yaml"


def test_explicit_backtest_scenario_override_is_respected(tmp_path: Path):
    custom = tmp_path / "scenario.yaml"
    custom.write_text("vpps: []\n", encoding="utf-8")
    assert resolve_scenario_path("p2p", custom) == custom


def test_latest_market_rosters_have_four_llm_agents_and_mirrors():
    for market_mode in ("p2p", "realprice"):
        info = inspect_scenario(resolve_scenario_path(market_mode))
        assert info.hybrid_count == 4
        assert info.mirror_count == 4
        assert len(info.hybrid_names) == 4


def test_strict_llm_connection_probe_failure_exits(monkeypatch: pytest.MonkeyPatch):
    class Settings:
        llm_api_key = "key"
        llm_key_file = "key.txt"
        llm_base_url = "https://example.invalid/v1"
        llm_model = "deepseek-v4-pro"
        llm_provider = "opencode"
        llm_timeout_sec = 120.0

    monkeypatch.setattr(
        "eflux.backtest.runner.validate_llm_connection",
        lambda **kwargs: (False, "probe failed"),
    )

    with pytest.raises(BacktestError, match="strict LLM connection validation failed: probe failed"):
        _validate_live_strict_llm(Settings())


@pytest.mark.asyncio
async def test_backtest_strict_llm_refresh_raises_instead_of_fallback():
    class BadClient:
        async def chat(self, messages, *, temperature=0.2):
            raise RuntimeError("endpoint down")

    class Agent:
        strategist = LLMStrategist(client=BadClient(), raise_errors=True)

    class Battery:
        soc_frac = 0.5

    class State:
        pnl = 0.0

    class VPP:
        name = "strict-llm"
        agent = Agent()
        battery = Battery()
        state = State()

    class Sim:
        def my_managed_vpps(self):
            return [VPP()]

    market = MarketSnapshot(
        sim_ts=datetime(2026, 1, 1, tzinfo=UTC),
        best_bid=None,
        best_ask=None,
        last_price=None,
        mid_price=None,
    )

    with pytest.raises(BacktestError, match="strict LLM refresh failed for strict-llm"):
        await _refresh_llm_fleet(Sim(), market)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_backtest_strict_llm_refresh_retries_transient_failure():
    # A single transient blip (empty completion) must not abort the run: the fleet refresh
    # retries and succeeds, so one flaky call can't discard a multi-hour backtest.
    class FlakyStrategist:
        hard_timeout_sec = 1.0

        def __init__(self):
            self.calls = 0

        async def arefresh(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("empty LLM response (completion budget exhausted?)")
            return None

    strat = FlakyStrategist()

    class Agent:
        strategist = strat

    class Battery:
        soc_frac = 0.5

    class State:
        pnl = 0.0

    class VPP:
        name = "flaky-llm"
        agent = Agent()
        battery = Battery()
        state = State()

    class Sim:
        def my_managed_vpps(self):
            return [VPP()]

    market = MarketSnapshot(
        sim_ts=datetime(2026, 1, 1, tzinfo=UTC),
        best_bid=None,
        best_ask=None,
        last_price=None,
        mid_price=None,
    )

    calls = await _refresh_llm_fleet(Sim(), market, max_attempts=3, retry_backoff_sec=0.0)  # type: ignore[arg-type]
    assert calls == 1
    assert strat.calls == 2  # failed once, succeeded on the retry


def test_sample_aggregate_records_p2p_book_prices():
    from eflux.backtest.runner import _sample_aggregate

    class Lvl:
        def __init__(self, price):
            self.price = price

    class Book:
        def best_bid(self):
            return Lvl(49.0)

        def best_ask(self):
            return Lvl(51.0)

    class Engine:
        last_price = 50.0
        book = Book()

    class St:
        load_kw = 10.0
        pv_kw = 4.0
        wind_kw = 1.0
        net_kw = -5.0

    class VPP:
        state = St()

    class Sim:
        def __init__(self):
            self.vpps = {1: VPP()}
            self.engine = Engine()

    row = _sample_aggregate(Sim(), datetime(2026, 1, 1, tzinfo=UTC), 0, -3.5)  # type: ignore[arg-type]
    assert row["lmp"] == -3.5  # CAISO reference unchanged
    assert row["p2p_last_price"] == 50.0  # peer clearing price now recorded
    assert row["p2p_best_bid"] == 49.0
    assert row["p2p_best_ask"] == 51.0
    assert row["p2p_mid"] == 50.0
    assert row["total_renew_kw"] == 5.0


def test_sample_aggregate_blank_p2p_prices_without_book():
    # realprice-style: no peer book -> peer-price columns are blank, not a crash.
    from eflux.backtest.runner import _sample_aggregate

    class Engine:
        last_price = None
        book = None

    class St:
        load_kw = 6.0
        pv_kw = 0.0
        wind_kw = 0.0
        net_kw = 6.0

    class VPP:
        state = St()

    class Sim:
        def __init__(self):
            self.vpps = {1: VPP()}
            self.engine = Engine()

    row = _sample_aggregate(Sim(), datetime(2026, 1, 1, tzinfo=UTC), 0, 42.0)  # type: ignore[arg-type]
    assert row["p2p_last_price"] is None
    assert row["p2p_best_bid"] is None and row["p2p_best_ask"] is None
    assert row["p2p_mid"] is None


def test_real_price_points_counts_loaded_rows():
    class RealData:
        price = (10.0, 11.0, 12.0)

    assert _real_price_points(None) == 0
    assert _real_price_points(RealData()) == 3


def test_historical_quote_falls_back_honestly_without_real_prices():
    # No real prices -> flat fallback, explicitly NOT a historical replay.
    quote = _historical_quote(
        datetime(2026, 5, 25, 9, tzinfo=UTC),
        real_data=None,
        price_is_real=False,
        region="caiso_sp15",
        node="TH_SP15_GEN-APND",
        fee=Decimal("2.0"),
    )
    assert quote.status == "synthetic"
    assert quote.is_real_price is False
    assert Decimal(str(quote.raw_lmp)) == _FALLBACK_PRICE


def test_historical_quote_replays_real_prices_when_available():
    class RealData:
        def price_at(self, ts):
            return 73.5

    quote = _historical_quote(
        datetime(2026, 5, 25, 9, tzinfo=UTC),
        real_data=RealData(),
        price_is_real=True,
        region="caiso_sp15",
        node="TH_SP15_GEN-APND",
        fee=Decimal("2.0"),
    )
    assert quote.status == "real"
    assert quote.is_real_price is True
    assert Decimal(str(quote.raw_lmp)) == Decimal("73.5")
