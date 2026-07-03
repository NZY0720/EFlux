"""Shared obs/action encoding for the structured-action PPO path.

The training env (`primitive_env`) and the live policy (`primitive_agent`) must agree
exactly on how an `AgentContext` + `ValuationSignal` becomes an observation vector, and
how a raw policy vector becomes a `StrategyAction`. Keeping both in one module guarantees
train/serve parity (design note §5.2: PPO observes market + DER + imbalance + SOC +
valuation signals, and acts in the structured primitive space).
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from eflux.agents.base import AgentContext
from eflux.agents.strategy.schema import StrategyAction, StrategyMode
from eflux.agents.valuation import ValuationSignal

PRICE_REF = 50.0  # default normalization scale (back-compat / tests / synthetic training)

# The *fixed* price scale every PPO price channel is normalized by (obs ratios + the reward's
# inventory mark). It is a constant per run/checkpoint — set once at training time to the
# trailing-month CAISO mean and carried in the checkpoint — NEVER the live tick. Keeping it
# fixed is what preserves the price-level signal: when the live LMP runs above this scale the
# obs ratios (mid/scale, fair_buy/scale, …) rise above 1.0 and the policy can see it. If the
# scale tracked the live price those ratios would collapse to ~1.0 and the policy would go
# blind to whether prices are high or low. See data/caiso_reference.py.
_scale = PRICE_REF


def price_ref_scale() -> float:
    """The current fixed PPO price-normalization scale."""
    return _scale


def set_price_ref_scale(value: float | None) -> None:
    """Set the process-wide normalization scale (training / scenario-load / eval entry points
    own this). A falsy/non-positive value resets to the default so callers degrade safely."""
    global _scale
    _scale = float(value) if value and float(value) > 0 else PRICE_REF

# The primitive set PPO chooses among (argmax over the first N_MODES action logits).
# A small, safe set: stand down, trade the imbalance either way, or work the battery.
PRIMITIVE_MODES: list[StrategyMode] = [
    StrategyMode.NOOP,
    StrategyMode.LIQUIDATE_SURPLUS,
    StrategyMode.COVER_DEFICIT,
    StrategyMode.BATTERY_ARBITRAGE,
]
N_MODES = len(PRIMITIVE_MODES)
N_PARAMS = 4  # aggressiveness, qty_fraction, price_offset_bps, soc_target
ENCODING_V1 = 1
ENCODING_V2 = 2
ACTION_DIM_V1 = N_MODES + N_PARAMS
ACTION_DIM = ACTION_DIM_V1
ACTION_DIM_V2 = ACTION_DIM_V1 + 1
LOG_MULT_MAX = math.log(2.5)
OBS_DIM = 18


def action_dim(version: int) -> int:
    if version == ENCODING_V1:
        return ACTION_DIM_V1
    if version == ENCODING_V2:
        return ACTION_DIM_V2
    raise ValueError(f"unsupported PPO encoding version: {version}")


def encoding_version_for_action_dim(dim: int) -> int:
    if dim == ACTION_DIM_V1:
        return ENCODING_V1
    if dim == ACTION_DIM_V2:
        return ENCODING_V2
    raise ValueError(f"unsupported PPO action dimension: {dim}")


def infer_encoding_version(state_dict: Mapping[str, object]) -> int:
    """Infer V1/V2 from the actor output-layer weight rows in a checkpoint state_dict."""
    if "state_dict" in state_dict and isinstance(state_dict["state_dict"], Mapping):
        return infer_encoding_version(state_dict["state_dict"])  # type: ignore[arg-type]
    for key in ("actor_mean.weight", "net.4.weight"):
        tensor = state_dict.get(key)
        if tensor is not None:
            rows = int(tensor.shape[0])  # torch.Tensor and np.ndarray both expose shape.
            return encoding_version_for_action_dim(rows)
    raise ValueError("cannot infer PPO encoding version: missing actor output weight")


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def encode_obs(ctx: AgentContext, valuation: ValuationSignal) -> np.ndarray:
    """AgentContext + ValuationSignal → fixed-width observation (OBS_DIM)."""
    m = ctx.market
    pr = price_ref_scale()
    mid = float(m.mid_price) if m.mid_price is not None else (float(m.last_price) if m.last_price is not None else pr)
    mid = max(mid, 1e-3)
    bb = float(m.best_bid) if m.best_bid is not None else None
    ba = float(m.best_ask) if m.best_ask is not None else None
    spread = ((ba - bb) / mid) if (bb is not None and ba is not None) else 0.0
    last = float(m.last_price) if m.last_price is not None else mid
    hour = ctx.state.sim_ts.hour + ctx.state.sim_ts.minute / 60.0
    cap = max(ctx.params.battery_kwh, 1e-3)
    return np.array(
        [
            ctx.state.pv_kw / max(ctx.params.pv_kw_peak, 1e-3),
            ctx.state.load_kw / max(ctx.params.load_kw_base * 2.0, 1e-3),
            ctx.battery.soc_frac,
            math.sin(2 * math.pi * hour / 24.0),
            math.cos(2 * math.pi * hour / 24.0),
            (bb - mid) / mid if bb is not None else 0.0,
            (ba - mid) / mid if ba is not None else 0.0,
            mid / pr,
            spread,
            last / pr,
            # valuation channels (design note §5.2)
            valuation.surplus_kwh / cap,
            valuation.deficit_kwh / cap,
            valuation.fair_buy_price / pr,
            valuation.fair_sell_price / pr,
            valuation.battery_sell_price / pr,
            valuation.battery_buy_price / pr,
            valuation.soc_pressure,
            ctx.open_orders_net_kwh / cap,
        ],
        dtype=np.float32,
    )


def _logit(p: float) -> float:
    p = min(1.0 - 1e-3, max(1e-3, p))
    return max(-5.0, min(5.0, math.log(p / (1.0 - p))))


def _atanh_clamped(x: float) -> float:
    x = max(-1.0 + 1e-3, min(1.0 - 1e-3, x))
    return max(-5.0, min(5.0, math.atanh(x)))


def encode_action(action: StrategyAction, *, version: int = ENCODING_V1) -> np.ndarray:
    """StrategyAction → a raw policy vector that decode_action maps back to it — the
    supervised target for behavior cloning. Inverse of decode_action up to the squash
    clamps; modes outside the PPO set clone to NOOP."""
    vec = np.full(action_dim(version), -1.0, dtype=np.float32)
    try:
        idx = PRIMITIVE_MODES.index(action.mode)
    except ValueError:
        idx = 0  # NOOP
    vec[idx] = 1.0
    p = vec[N_MODES:]
    p[0] = _logit(action.aggressiveness)
    p[1] = _logit(action.qty_fraction)
    bps = max(-1.0 + 1e-3, min(1.0 - 1e-3, action.price_offset_bps / 50.0))
    p[2] = max(-5.0, min(5.0, math.atanh(bps)))
    p[3] = _logit(action.soc_target)
    if version == ENCODING_V2:
        ratio = 1.0 if action.price_target_mult is None else float(action.price_target_mult)
        ratio = max(math.exp(-LOG_MULT_MAX), min(math.exp(LOG_MULT_MAX), ratio))
        p[4] = _atanh_clamped(math.log(ratio) / LOG_MULT_MAX)
    return vec


def decode_action(vec: np.ndarray, *, version: int = ENCODING_V1) -> StrategyAction:
    """Raw policy vector (ACTION_DIM) → a bounded StrategyAction. The first N_MODES
    components are mode logits (argmax picks the primitive); the rest are squashed
    into their parameter ranges."""
    vec = np.asarray(vec, dtype=np.float32).flatten()
    expected = action_dim(version)
    if vec.shape[0] != expected:
        raise ValueError(f"expected PPO action width {expected} for encoding V{version}, got {vec.shape[0]}")
    mode = PRIMITIVE_MODES[int(np.argmax(vec[:N_MODES]))]
    p = vec[N_MODES:]
    return StrategyAction(
        mode=mode,
        aggressiveness=_sigmoid(float(p[0])),
        qty_fraction=_sigmoid(float(p[1])),
        price_offset_bps=math.tanh(float(p[2])) * 50.0,
        soc_target=_sigmoid(float(p[3])),
        price_target_mult=math.exp(math.tanh(float(p[4])) * LOG_MULT_MAX)
        if version == ENCODING_V2
        else None,
    )
