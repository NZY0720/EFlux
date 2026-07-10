from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.hybrid import HybridPolicyAgent
from eflux.agents.reflective.llm_client import LLMBudgetExceeded, LLMClient, LLMUsageBudget
from eflux.agents.reflective.strategist import LLMStrategist
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


class _ResponseClient:
    def __init__(self, payload: dict):
        self.payload = payload

    async def post(self, url, *, json, headers):
        return httpx.Response(200, json=self.payload, request=httpx.Request("POST", url))

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_llm_client_tracks_provider_usage_and_stops_before_budget_breach():
    budget = LLMUsageBudget(
        max_cost_usd=0.00011,
        input_cost_per_million_tokens=1.0,
        output_cost_per_million_tokens=1.0,
    )
    client = LLMClient(
        base_url="https://example.test/v1",
        api_key="secret",
        model="model",
        budget=budget,
    )
    await client._client.aclose()
    client._client = _ResponseClient(
        {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
    )

    assert await client.chat([{"role": "user", "content": "x"}], max_tokens=100) == "{}"
    assert client.usage == {
        "calls": 1,
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "estimated_cost_usd": 0.00003,
        "reserved_cost_usd": 0.0,
        "max_cost_usd": 0.00011,
        "remaining_cost_usd": 0.00008,
    }
    with pytest.raises(LLMBudgetExceeded):
        await client.chat([{"role": "user", "content": "x"}], max_tokens=100)


def _ctx() -> AgentContext:
    params = VPPParams()
    ts = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    state = VPPState(sim_ts=ts, pnl=Decimal("1.0"), pv_kw=5.0, load_kw=1.0)
    state.update_net()
    battery = Battery(capacity_kwh=10.0, max_power_kw=3.0, soc_kwh=5.0)
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(kw_peak=5.0),
        battery=battery,
        load=FlexibleLoad(base_kw=1.0),
        market=MarketSnapshot(ts, Decimal("48"), Decimal("52"), Decimal("50"), Decimal("50")),
        rng=random.Random(1),
        tick_duration_h=1 / 120,
        projected_net_kwh=0.5,
        contracted_net_kwh=0.2,
    )


@pytest.mark.asyncio
async def test_hybrid_sends_windowed_trade_imbalance_and_rejection_feedback():
    class CaptureClient:
        def __init__(self):
            self.payloads: list[dict] = []

        async def chat(self, messages, *, temperature=0.2):
            self.payloads.append(json.loads(messages[1]["content"]))
            return '{"risk_budget": 0.8}'

    client = CaptureClient()
    agent = HybridPolicyAgent(
        strategist=LLMStrategist(client=client),
        refresh_every_n_ticks=1,
    )
    ctx = _ctx()
    agent.decide(ctx)
    await agent._reflection_task

    agent.record_trade({"side": "sell", "price": "50", "qty": "0.4", "cash_usd": "0.02"})
    ctx.state.sim_ts += timedelta(seconds=30)
    ctx.state.pnl = Decimal("1.02")
    ctx.risk_rejections_total = 2
    ctx.realized_imbalance_abs_kwh_total = 0.15
    ctx.contracted_net_kwh = 0.45
    agent.decide(ctx)
    await agent._reflection_task

    window = client.payloads[-1]["performance_window"]
    latest = window[-1]
    assert len(window) == 2
    assert latest["pnl_delta_usd"] == pytest.approx(0.02)
    assert latest["trade_count"] == 1
    assert latest["traded_kwh"] == pytest.approx(0.4)
    assert latest["trade_cash_usd"] == pytest.approx(0.02)
    assert latest["rejection_delta"] == 2
    assert latest["realized_abs_imbalance_delta_kwh"] == pytest.approx(0.15)
    assert latest["residual_contract_exposure_kwh"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_strategist_treats_budget_exhaustion_as_skip_not_live_failure():
    class BudgetClient:
        async def chat(self, messages, *, temperature=0.2):
            raise LLMBudgetExceeded("ceiling reached")

    strategist = LLMStrategist(client=BudgetClient())
    result = await strategist.arefresh(
        recent_pnl=[],
        soc_frac=0.5,
        best_bid=None,
        best_ask=None,
        last_price=None,
    )
    assert result is None
    assert strategist.skipped_count == 1
    assert strategist.fail_count == 0
    assert "ceiling reached" in strategist.reflection_log[-1]["error"]
