"""Strict archived and fresh LLM strategists for Release evaluation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from eflux.agents.base import AgentContext
from eflux.agents.character import endowment_summary
from eflux.agents.hybrid import HybridPolicyAgent, StrategyAgent
from eflux.agents.reflective.llm_client import LLMClient, LLMUsageMeter
from eflux.agents.reflective.strategist import (
    LLMStrategist,
    compact_forecast_for_strategist,
)
from eflux.config import get_settings
from eflux.ecosystem.runtime import agent_factory_from_release


def _canonical_prompt_sha256(messages: list[dict[str, str]]) -> str:
    payload = json.dumps(messages, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class ArchivedTranscriptClient:
    """LLM-compatible client that fails closed unless the prompt matches the archive."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        if not records:
            raise ValueError("deterministic LLM replay requires a non-empty transcript archive")
        self._records = [dict(record) for record in records]
        self._index = 0
        self._usage = {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "estimated_cost_usd": 0.0,
        }

    @property
    def model(self) -> str:
        return str(self._records[0].get("model") or "archived-unknown")

    @property
    def usage(self) -> dict[str, float | int]:
        return dict(self._usage)

    @property
    def consumed(self) -> int:
        return self._index

    @property
    def total(self) -> int:
        return len(self._records)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        if self._index >= len(self._records):
            raise RuntimeError("deterministic LLM transcript archive is exhausted")
        record = self._records[self._index]
        expected_hash = str(record.get("prompt_sha256") or "")
        actual_hash = _canonical_prompt_sha256(messages)
        if not expected_hash or expected_hash != actual_hash:
            raise RuntimeError(
                f"deterministic LLM transcript prompt hash mismatch at call {self._index + 1}"
            )
        archived_temperature = record.get("temperature")
        if archived_temperature is not None and not math.isclose(
            float(archived_temperature), float(temperature), abs_tol=1e-12
        ):
            raise RuntimeError("deterministic LLM transcript temperature mismatch")
        archived_max_tokens = record.get("max_tokens")
        if archived_max_tokens is not None and int(archived_max_tokens) != int(max_tokens):
            raise RuntimeError("deterministic LLM transcript max_tokens mismatch")
        if record.get("error"):
            raise RuntimeError(f"archived LLM call failed: {record['error']}")
        response = record.get("response")
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("archived LLM transcript has no usable response")
        expected_response_hash = str(record.get("response_sha256") or "")
        actual_response_hash = hashlib.sha256(response.encode()).hexdigest()
        if expected_response_hash and expected_response_hash != actual_response_hash:
            raise RuntimeError("archived LLM response hash mismatch")
        usage = record.get("usage_delta") or {}
        for key in self._usage:
            value = usage.get(key, 0)
            self._usage[key] += float(value) if key == "estimated_cost_usd" else int(value)
        self._index += 1
        return response

    async def aclose(self) -> None:
        return None

    def assert_fully_consumed(self) -> None:
        if self._index != len(self._records):
            raise RuntimeError(
                f"deterministic LLM transcript has {len(self._records) - self._index} unused call(s)"
            )


class HistoricalLLMHybridAgent(HybridPolicyAgent):
    """Hybrid agent whose strategist is refreshed synchronously on simulation time."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self._llm_loop = asyncio.new_event_loop()

    def _maybe_refresh_guidance(self, ctx: AgentContext) -> None:
        self._ticks += 1
        refresh = max(1, int(self.refresh_every_n_ticks))
        if (self._ticks - 1) % refresh != 0:
            return
        arefresh = getattr(self.strategist, "arefresh", None)
        if not callable(arefresh):
            raise RuntimeError("historical LLM evaluation has no callable strategist")
        market = ctx.market
        grid = market.external_market
        silence_window = None
        if ctx.silence_ticks and ctx.silence_reasons:
            silence_window = {
                "silent_ticks": ctx.silence_ticks,
                "reasons": dict(ctx.silence_reasons),
            }
        self._llm_loop.run_until_complete(
            arefresh(
                recent_pnl=[float(row["pnl_usd"]) for row in self._performance_window],
                soc_frac=ctx.battery.soc_frac,
                best_bid=float(market.best_bid) if market.best_bid is not None else None,
                best_ask=float(market.best_ask) if market.best_ask is not None else None,
                last_price=float(market.last_price) if market.last_price is not None else None,
                regime_note=self._regime_note(ctx),
                market_mode=market.market_mode,
                grid_raw_lmp=float(grid.raw_lmp) if grid is not None else None,
                grid_import_price=float(grid.import_price) if grid is not None else None,
                grid_export_price=float(grid.export_price) if grid is not None else None,
                grid_status=grid.status if grid is not None else None,
                forecast=compact_forecast_for_strategist(ctx.forecast),
                endowment=endowment_summary(ctx.params),
                character=self.character.to_public(),
                performance_window=list(self._performance_window),
                silence_window=silence_window,
            )
        )

    def close_historical_llm(self, *, require_archive_consumed: bool) -> None:
        client = getattr(self.strategist, "client", None)
        if require_archive_consumed:
            assert_consumed = getattr(client, "assert_fully_consumed", None)
            if callable(assert_consumed):
                assert_consumed()
        close = getattr(client, "aclose", None)
        if callable(close):
            self._llm_loop.run_until_complete(close())
        self._llm_loop.close()


def archived_transcripts_for_seed(release: Mapping[str, Any], seed: int) -> list[dict[str, Any]]:
    state = release.get("state")
    if not isinstance(state, Mapping):
        raise ValueError("release.state must contain deterministic LLM transcripts")
    archives = state.get("llm_replay_archives")
    if isinstance(archives, Mapping):
        records = archives.get(str(seed), archives.get(seed))
    else:
        records = state.get("llm_replay_archive")
    if not isinstance(records, list) or not all(isinstance(item, Mapping) for item in records):
        raise ValueError(f"release has no LLM transcript archive for seed {seed}")
    return [dict(item) for item in records]


def platform_fresh_llm_client(release: Mapping[str, Any]) -> LLMClient:
    settings = get_settings()
    if not settings.llm_api_key or not settings.llm_base_url or not settings.llm_model:
        raise ValueError("Fresh-LLM Replay requires configured platform provider credentials")
    recipe = release.get("recipe")
    llm = recipe.get("llm") if isinstance(recipe, Mapping) else None
    costs = llm.get("cost_estimate") if isinstance(llm, Mapping) else None
    input_rate = (
        costs.get("input_usd_per_million_tokens")
        if isinstance(costs, Mapping)
        else settings.llm_input_cost_per_million_tokens
    )
    output_rate = (
        costs.get("output_usd_per_million_tokens")
        if isinstance(costs, Mapping)
        else settings.llm_output_cost_per_million_tokens
    )
    meter = LLMUsageMeter(
        input_cost_per_million_tokens=float(input_rate),
        output_cost_per_million_tokens=float(output_rate),
    )
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_sec=settings.llm_timeout_sec,
        usage_meter=meter,
    )


def build_historical_llm_agent(
    release: Mapping[str, Any],
    *,
    client: object,
) -> HistoricalLLMHybridAgent:
    recipe = release.get("recipe")
    if not isinstance(recipe, Mapping) or not isinstance(recipe.get("llm"), Mapping):
        raise ValueError("historical LLM evaluation requires release.recipe.llm")
    llm = recipe["llm"]
    base = agent_factory_from_release(release, learning=False)
    if not isinstance(base, StrategyAgent):
        raise ValueError("LLM guidance is supported only for scripted/strategy/PPO releases")
    refresh = llm.get("guidance_refresh_every_n_ticks")
    if refresh is None:
        seconds = float(llm.get("guidance_refresh_interval_seconds", 300))
        refresh = max(1, math.ceil(seconds / 300.0))
    strategist = LLMStrategist(
        client=client,
        temperature=float(llm.get("temperature", 0.3)),
        max_tokens=int(llm.get("max_tokens", 4096)),
        hard_timeout_sec=float(llm.get("timeout_seconds", llm.get("timeout_sec", 180))),
        raise_errors=True,
        system_prompt_override=str(llm["system_prompt"]),
        prompt_template_override=str(llm["prompt_template"]),
        prompt_template_version=str(llm.get("prompt_template_version", "release-v1")),
    )
    fallback = str(recipe.get("fallback_strategy", "safe_hold")).lower()
    fallback_policy = "truthful" if fallback == "truthful" else "hold"
    return HistoricalLLMHybridAgent(
        price_ref=base.price_ref,
        min_qty=base.min_qty,
        demand_beta=base.demand_beta,
        price_cap_mult=base.price_cap_mult,
        use_forecast=base.use_forecast,
        executor=base._policy,
        strategist=strategist,
        fallback_policy=fallback_policy,
        refresh_every_n_ticks=int(refresh),
        character=base.character,
    )
