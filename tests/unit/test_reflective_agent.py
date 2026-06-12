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
from eflux.agents.reflective.prompt import HintParseError, ReflectionHints, parse_hints
from eflux.agents.truthful import TruthfulAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _ctx(*, pv_kw: float = 5.0, load_kw: float = 1.0) -> AgentContext:
    params = VPPParams()
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=5.0, pv_kw=pv_kw, load_kw=load_kw)
    state.update_net()
    # Agents quote from the accumulated untraded balance (maintained by the
    # runner). With tick_duration_h=1.0 one tick's accumulation equals net_kw.
    state.pending_net_kwh = state.net_kw * 1.0
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


def test_parse_hints_raises_on_malformed():
    # Garbage must surface as a parse failure (recorded as ok=False upstream),
    # not silently become a "successful" neutral reflection.
    with pytest.raises(HintParseError):
        parse_hints("totally not json")


def test_parse_hints_raises_on_truncated_json():
    with pytest.raises(HintParseError):
        parse_hints('{"price_adjust": 0.05, "qty_scale":')


def test_parse_hints_extracts_object_from_surrounding_prose():
    h = parse_hints(
        'Sure! Based on the data I suggest:\n{"price_adjust": -0.03, "qty_scale": 1.1, '
        '"rationale": "undercut"}\nLet me know if you need more.'
    )
    assert h.price_adjust == -0.03
    assert h.qty_scale == 1.1


def test_parse_hints_takes_first_object_of_multi_object_response():
    # The old greedy {.*} regex spanned first-to-last brace and failed to decode this.
    h = parse_hints(
        '{"price_adjust": 0.02, "qty_scale": 1.0, "rationale": "a"} '
        '{"price_adjust": 0.19, "qty_scale": 1.4, "rationale": "b"}'
    )
    assert h.price_adjust == 0.02
    assert h.rationale == "a"


def test_parse_hints_raises_on_non_numeric_fields():
    with pytest.raises(HintParseError):
        parse_hints('{"price_adjust": "up a lot", "qty_scale": 1.0, "rationale": "x"}')


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
        # Audit trail recorded the successful reflection.
        assert agent.ok_count == 1
        assert agent.fail_count == 0
        assert agent.last_ok_ts is not None
        entry = agent.reflection_log[-1]
        assert entry["ok"] is True
        assert entry["price_adjust"] == 0.05
        assert entry["qty_scale"] == 0.8
        assert entry["rationale"] == "tightened"
        assert entry["error"] is None

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
        # Failure is recorded in the audit trail with the error message.
        assert agent.fail_count >= 1
        assert agent.ok_count == 0
        entry = agent.reflection_log[-1]
        assert entry["ok"] is False
        assert "RuntimeError: boom" in entry["error"]

    asyncio.run(run())


def test_reflective_records_failure_on_garbage_llm_response():
    """A response with no JSON is logged as a FAILED reflection (ok=False) and
    the previous hints stay in effect."""
    fake = _FakeLLM("I think you should probably bid higher? Good luck!")
    agent = ReflectiveAgent(
        llm_client=fake,  # type: ignore[arg-type]
        inner=TruthfulAgent(price_ref=Decimal("50")),
        reflect_every_n_ticks=1,
    )

    async def run():
        agent.decide(_ctx(pv_kw=0.5, load_kw=3.0))
        await asyncio.sleep(0.05)
        intents = agent.decide(_ctx(pv_kw=0.5, load_kw=3.0))
        assert intents[0].price == Decimal("50.0000")  # hints unchanged
        assert agent.fail_count == 1
        assert agent.ok_count == 0
        entry = agent.reflection_log[-1]
        assert entry["ok"] is False
        assert "HintParseError" in entry["error"]

    asyncio.run(run())


def test_reflective_reflection_reschedules_after_failure():
    """Regression: the in-flight flag must clear after a failed round-trip so the
    next interval triggers a fresh reflection instead of stalling forever."""
    fake = _FakeLLM("not json")
    agent = ReflectiveAgent(
        llm_client=fake,  # type: ignore[arg-type]
        inner=TruthfulAgent(price_ref=Decimal("50")),
        reflect_every_n_ticks=1,
    )

    async def run():
        for _ in range(3):
            agent.decide(_ctx(pv_kw=0.5, load_kw=3.0))
            await asyncio.sleep(0.02)
        assert len(fake.calls) == 3
        assert agent.fail_count == 3

    asyncio.run(run())


def test_parse_hints_reads_and_clamps_lesson():
    h = parse_hints(
        '{"price_adjust": 0.05, "qty_scale": 1.0, "rationale": "r", "lesson": "'
        + "x" * 400
        + '"}'
    )
    assert len(h.lesson) == 160


def test_build_system_prompt_appends_persona():
    from eflux.agents.reflective.prompt import SYSTEM_PROMPT, build_system_prompt

    assert build_system_prompt(None) == SYSTEM_PROMPT
    composed = build_system_prompt("You are a ruthless arbitrageur.")
    assert composed.startswith(SYSTEM_PROMPT)
    assert "ruthless arbitrageur" in composed


def test_reflect_offset_staggers_trigger_tick():
    """offset=1, interval=3 → reflections fire on ticks 1, 4, 7… not 3, 6, 9…"""
    fake = _FakeLLM('{"price_adjust": 0.0, "qty_scale": 1.0, "rationale": "x"}')
    agent = ReflectiveAgent(
        llm_client=fake,  # type: ignore[arg-type]
        inner=TruthfulAgent(price_ref=Decimal("50")),
        reflect_every_n_ticks=3,
        reflect_offset_ticks=1,
    )

    async def run():
        agent.decide(_ctx())  # tick 1 → fires
        await asyncio.sleep(0.02)
        assert len(fake.calls) == 1
        agent.decide(_ctx())  # tick 2
        agent.decide(_ctx())  # tick 3 — would fire without the offset
        await asyncio.sleep(0.02)
        assert len(fake.calls) == 1
        agent.decide(_ctx())  # tick 4 → fires
        await asyncio.sleep(0.02)
        assert len(fake.calls) == 2

    asyncio.run(run())


def test_locked_gate_skips_cycle_without_failing():
    """When another agent holds the shared LLM gate at trigger time, the cycle
    is skipped (counted) — never queued against the slow endpoint, never
    recorded as a failure."""
    fake = _FakeLLM('{"price_adjust": 0.0, "qty_scale": 1.0, "rationale": "x"}')
    gate = asyncio.Semaphore(1)
    agent = ReflectiveAgent(
        llm_client=fake,  # type: ignore[arg-type]
        inner=TruthfulAgent(price_ref=Decimal("50")),
        reflect_every_n_ticks=1,
        llm_gate=gate,
    )

    async def run():
        await gate.acquire()  # someone else is mid-call
        try:
            agent.decide(_ctx())
            await asyncio.sleep(0.02)
            assert fake.calls == []
            assert agent.skipped_count == 1
            assert agent.fail_count == 0
        finally:
            gate.release()
        # Gate free again → next cycle reflects normally (through the gate).
        agent.decide(_ctx())
        await asyncio.sleep(0.02)
        assert len(fake.calls) == 1

    asyncio.run(run())


def test_hint_outcome_attribution_closes_window_and_feeds_prompt(tmp_path):
    """The learning loop end-to-end: reflection N's hints + the PnL/trades
    observed until reflection N+1 become a memory record (persisted), and
    reflection N+1's prompt carries it under past_hint_outcomes."""
    import json as _json

    from eflux.agents.reflective.memory import AgentMemory

    fake = _FakeLLM(
        '{"price_adjust": 0.05, "qty_scale": 1.2, "rationale": "probe", '
        '"lesson": "test lesson"}'
    )
    memory = AgentMemory(tmp_path / "agent.jsonl")
    agent = ReflectiveAgent(
        llm_client=fake,  # type: ignore[arg-type]
        inner=TruthfulAgent(price_ref=Decimal("50")),
        reflect_every_n_ticks=2,
        memory=memory,
    )

    async def run():
        ctx = _ctx(pv_kw=0.5, load_kw=3.0)
        # Ticks 1-2: second tick triggers reflection #1 (opens the window).
        agent.decide(ctx)
        agent.decide(ctx)
        await asyncio.sleep(0.05)
        assert agent.ok_count == 1
        assert len(memory.records) == 0  # first reflection only opens a window

        # PnL moves while the hints are active.
        ctx.state.pnl = Decimal("3.5")
        agent.record_trade({"trade_id": 1, "side": "sell", "price": "52", "qty": "1"})

        # Ticks 3-4: tick 4 triggers reflection #2 → closes window #1.
        agent.decide(ctx)
        agent.decide(ctx)
        await asyncio.sleep(0.05)
        assert agent.ok_count == 2

        # The closed window was attributed and persisted.
        assert len(memory.records) == 1
        record = memory.records[0]
        assert record["hints"] == {"price_adjust": 0.05, "qty_scale": 1.2}
        assert record["lesson"] == "test lesson"
        assert record["window"]["pnl"] == 3.5
        assert record["window"]["trades"] == 1
        assert record["window"]["ticks"] == 2
        line = (tmp_path / "agent.jsonl").read_text().strip()
        assert _json.loads(line)["window"]["pnl"] == 3.5

        # …and reflection #2's prompt carried it.
        second_user_msg = fake.calls[1][1]["content"]
        payload = _json.loads(second_user_msg)
        outcomes = payload["past_hint_outcomes"]
        assert outcomes[0]["pa"] == 0.05
        assert outcomes[0]["pnl"] == 3.5
        assert outcomes[0]["lesson"] == "test lesson"

    asyncio.run(run())


def test_prompt_includes_market_trades_and_peers_excluding_self():
    """Learning from others: the prompt carries market-wide fills and peer LLM
    views from the runner-populated MarketSnapshot — minus the agent itself."""
    import json as _json

    fake = _FakeLLM('{"price_adjust": 0.0, "qty_scale": 1.0, "rationale": "x"}')
    agent = ReflectiveAgent(
        llm_client=fake,  # type: ignore[arg-type]
        inner=TruthfulAgent(price_ref=Decimal("50")),
        reflect_every_n_ticks=1,
    )

    async def run():
        ctx = _ctx()
        ctx.market.recent_trades = [
            {"price": 52.0, "qty": 1.0, "buyer": "datacenter-25", "seller": "gas-base-26"}
        ]
        ctx.market.peer_reflections = [
            {"vpp_id": 1, "name": "me", "pa": 0.1, "qs": 1.0, "rationale": "mine"},
            {"vpp_id": -7, "name": "llm-arb-aggressive", "pa": -0.05, "qs": 1.3, "rationale": "undercut"},
        ]
        agent.decide(ctx)  # ctx.vpp_id == 1 → own entry must be dropped
        await asyncio.sleep(0.02)

        payload = _json.loads(fake.calls[0][1]["content"])
        assert payload["recent_market_trades"][0]["seller"] == "gas-base-26"
        peers = payload["peer_llm_views"]
        assert len(peers) == 1
        assert peers[0]["name"] == "llm-arb-aggressive"
        assert "vpp_id" not in peers[0]

    asyncio.run(run())


def test_build_user_message_serializes_runner_trade_records():
    """Regression: runner trade records carry datetime/Decimal values; the prompt
    builder must not blow up on them (it did, once the LLM VPP actually traded)."""
    from eflux.agents.reflective.prompt import build_user_message

    msg = build_user_message(
        recent_pnl=[0.1, -0.2],
        recent_trades=[
            {
                "trade_id": 1,
                "side": "buy",
                "price": Decimal("20.0000"),
                "qty": Decimal("0.0100"),
                "cash": Decimal("0.2"),
                "counterparty_vpp_id": -3,
                "buy_vpp_id": -11,
                "sell_vpp_id": -3,
                "sim_ts": datetime.now(UTC),
                "wall_ts": datetime.now(UTC),
            }
        ],
        soc_frac=0.5,
        best_bid=20.0,
        best_ask=21.0,
        last_price=20.5,
    )
    assert '"trade_id": 1' in msg
