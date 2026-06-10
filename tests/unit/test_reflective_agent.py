"""Unit tests for the Reflective LLM agent.

The LLM is fully mocked here so tests run offline with no network / API key.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.reflective.agent import ReflectiveAgent
from eflux.agents.reflective.prompt import ReflectionHints, parse_hints
from eflux.agents.truthful import TruthfulAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import Battery, FlexibleLoad, PV


def _ctx(*, pv_kw: float = 5.0, load_kw: float = 1.0) -> AgentContext:
    params = VPPParams()
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=5.0, pv_kw=pv_kw, load_kw=load_kw)
    state.update_net()
    market = MarketSnapshot(
        sim_ts=state.sim_ts,
        best_bid=Decimal("48"),
        best_ask=Decimal("52"),
        last_price=Decimal("50"),
        mid_price=Decimal("50"),
    )
    return AgentContext(
        vpp_id=1, params=params, state=state,
        pv=PV(kw_peak=params.pv_kw_peak),
        battery=Battery(capacity_kwh=params.battery_kwh, max_power_kw=params.battery_kw_max),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=market, rng=random.Random(0), tick_duration_h=1.0,
    )


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict]] = []

    async def chat(self, messages, temperature: float = 0.2) -> str:
        self.calls.append(messages)
        return self.response

    async def aclose(self) -> None:
        pass


def test_parse_hints_extracts_json_from_plain_text():
    h = parse_hints('{"price_adjust": 0.05, "qty_scale": 1.2, "rationale": "ok"}')
    assert h.price_adjust == 0.05
    assert h.qty_scale == 1.2
    assert h.rationale == "ok"


def test_parse_hints_clamps_out_of_range():
    h = parse_hints('{"price_adjust": 5.0, "qty_scale": 100.0, "rationale": "wild"}')
    assert h.price_adjust == 0.20
    assert h.qty_scale == 1.5


def test_parse_hints_handles_markdown_fences():
    h = parse_hints("```json\n{\"price_adjust\": 0.1, \"qty_scale\": 0.9, \"rationale\": \"x\"}\n```")
    assert h.price_adjust == 0.1
    assert h.qty_scale == 0.9


def test_parse_hints_returns_defaults_on_malformed():
    h = parse_hints("totally not json")
    assert h.price_adjust == 0.0
    assert h.qty_scale == 1.0


def test_reflective_with_no_client_just_proxies_inner():
    """No LLM → behaves identically to the inner agent (no hints applied)."""
    agent = ReflectiveAgent(llm_client=None, inner=TruthfulAgent(price_ref=Decimal("50")))
    intents = agent.decide(_ctx(pv_kw=5.0, load_kw=1.0))
    # qty_scale=1.0, price_adjust=0.0 → same as TruthfulAgent.
    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert intents[0].qty == Decimal("4.0000")


def test_reflective_applies_price_and_qty_hints():
    agent = ReflectiveAgent(llm_client=None, inner=TruthfulAgent(price_ref=Decimal("50")))
    # Manually inject hints (skip the LLM call).
    agent._hints = ReflectionHints(price_adjust=0.10, qty_scale=0.5, rationale="test")  # type: ignore[attr-defined]
    intents = agent.decide(_ctx(pv_kw=5.0, load_kw=1.0))
    assert len(intents) == 1
    # Sell side + positive price_adjust → ask LOWER by 10% (we apply -sign for sells)
    # Base ask = 0 (markup_floor=0 by default) → impossible to test multiplicative; switch to buy case.
    # So test buy side which has clear baseline price = price_ref.
    intents = agent.decide(_ctx(pv_kw=0.5, load_kw=3.0))
    assert intents[0].side == "buy"
    # Base buy price = 50, +10% → 55. qty = 2.5 * 0.5 = 1.25
    assert intents[0].price == Decimal("55.0000")
    assert intents[0].qty == Decimal("1.2500")


def test_reflective_lifecycle_with_fake_llm():
    """End-to-end: fake LLM returns a hint; after enough ticks the hint takes effect."""
    fake = _FakeLLM('{"price_adjust": 0.05, "qty_scale": 0.8, "rationale": "tightened"}')
    agent = ReflectiveAgent(
        llm_client=fake,  # type: ignore[arg-type]  duck-typed
        inner=TruthfulAgent(price_ref=Decimal("50")),
        reflect_every_n_ticks=2,
    )

    async def run():
        # Tick 1: pre-reflection, hints are defaults.
        intents = agent.decide(_ctx(pv_kw=0.5, load_kw=3.0))
        assert intents[0].price == Decimal("50.0000")
        # Tick 2: triggers reflection (fire-and-forget). The new hints land on the
        # NEXT decision since the task runs asynchronously.
        agent.decide(_ctx(pv_kw=0.5, load_kw=3.0))
        # Give the fire-and-forget task a chance to run.
        await asyncio.sleep(0.05)
        # Tick 3: hints applied — price 50 * 1.05 = 52.5; qty 2.5 * 0.8 = 2.0
        intents = agent.decide(_ctx(pv_kw=0.5, load_kw=3.0))
        assert intents[0].price == Decimal("52.5000")
        assert intents[0].qty == Decimal("2.0000")
        assert len(fake.calls) == 1

    asyncio.run(run())


def test_reflective_falls_back_when_llm_errors():
    """A throwing LLM must NOT crash the agent; previous hints remain in effect."""
    class _BadLLM:
        async def chat(self, messages, temperature: float = 0.2):
            raise RuntimeError("boom")

    agent = ReflectiveAgent(
        llm_client=_BadLLM(),  # type: ignore[arg-type]
        inner=TruthfulAgent(price_ref=Decimal("50")),
        reflect_every_n_ticks=1,
    )

    async def run():
        agent.decide(_ctx(pv_kw=0.5, load_kw=3.0))
        await asyncio.sleep(0.05)  # let the errored task settle
        # Hints unchanged (zeros) — behaviour stays = inner.
        intents = agent.decide(_ctx(pv_kw=0.5, load_kw=3.0))
        assert intents[0].price == Decimal("50.0000")

    asyncio.run(run())
