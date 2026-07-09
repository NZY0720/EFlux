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

ACTION_PROFILE_P2P = "p2p"
ACTION_PROFILE_REALPRICE_GRID = "realprice_grid"

# The primitive set PPO chooses among (argmax over the first mode logits).
# A small, safe set: stand down, trade the imbalance either way, or work the battery.
PRIMITIVE_MODES_P2P: list[StrategyMode] = [
    StrategyMode.NOOP,
    StrategyMode.LIQUIDATE_SURPLUS,
    StrategyMode.COVER_DEFICIT,
    StrategyMode.BATTERY_ARBITRAGE,
]
PRIMITIVE_MODES = PRIMITIVE_MODES_P2P
PRIMITIVE_MODES_REALPRICE: list[StrategyMode] = [
    StrategyMode.NOOP,
    StrategyMode.COVER_DEFICIT,
    StrategyMode.LIQUIDATE_SURPLUS,
    StrategyMode.GRID_CHARGE_ON_DIP,
    StrategyMode.GRID_DISCHARGE_ON_PEAK,
    StrategyMode.WAIT_FOR_BETTER,
]
N_MODES = len(PRIMITIVE_MODES)
N_PARAMS = 4  # aggressiveness, qty_fraction, price_offset_bps, soc_target
ENCODING_V1 = 1
ENCODING_V2 = 2
ACTION_DIM_V1 = N_MODES + N_PARAMS
ACTION_DIM = ACTION_DIM_V1
ACTION_DIM_V2 = ACTION_DIM_V1 + 1
LOG_MULT_MAX = math.log(2.5)
OBS_V1 = 1
OBS_V3 = 3
N_FORECAST_CHANNELS = 6
OBS_DIM_V1 = 18
OBS_DIM = OBS_DIM_V1
OBS_DIM_V3 = OBS_DIM_V1 + N_FORECAST_CHANNELS


def normalize_action_profile(action_profile: str | None) -> str:
    profile = (action_profile or ACTION_PROFILE_P2P).strip().lower()
    if profile in {"p2p", "legacy", "p2p_legacy"}:
        return ACTION_PROFILE_P2P
    if profile in {"realprice", "realprice_grid", "grid"}:
        return ACTION_PROFILE_REALPRICE_GRID
    raise ValueError(f"unsupported PPO action profile: {action_profile}")


def action_profile_for_market(market_mode: str | None) -> str:
    return ACTION_PROFILE_REALPRICE_GRID if market_mode == "realprice" else ACTION_PROFILE_P2P


def primitive_modes_for(
    market_mode: str | None = None,
    *,
    action_profile: str | None = None,
) -> list[StrategyMode]:
    profile = normalize_action_profile(action_profile) if action_profile else action_profile_for_market(market_mode)
    if profile == ACTION_PROFILE_REALPRICE_GRID:
        return PRIMITIVE_MODES_REALPRICE
    return PRIMITIVE_MODES_P2P


def action_dim(
    version: int,
    *,
    modes: list[StrategyMode] | tuple[StrategyMode, ...] | None = None,
    market_mode: str | None = None,
    action_profile: str | None = None,
) -> int:
    mode_count = len(modes) if modes is not None else len(primitive_modes_for(market_mode, action_profile=action_profile))
    if version == ENCODING_V1:
        return mode_count + N_PARAMS
    if version == ENCODING_V2:
        return mode_count + N_PARAMS + 1
    raise ValueError(f"unsupported PPO encoding version: {version}")


def action_profile_for_action_dim(dim: int) -> str:
    if dim in (8, 9):
        return ACTION_PROFILE_P2P
    if dim in (10, 11):
        return ACTION_PROFILE_REALPRICE_GRID
    raise ValueError(f"unsupported PPO action dimension: {dim}")


def encoding_version_for_action_dim(dim: int) -> int:
    if dim in (8, 10):
        return ENCODING_V1
    if dim in (9, 11):
        return ENCODING_V2
    raise ValueError(f"unsupported PPO action dimension: {dim}")


def infer_action_dim(state_dict: Mapping[str, object]) -> int:
    if "state_dict" in state_dict and isinstance(state_dict["state_dict"], Mapping):
        return infer_action_dim(state_dict["state_dict"])  # type: ignore[arg-type]
    for key in ("actor_mean.weight", "net.4.weight"):
        tensor = state_dict.get(key)
        if tensor is not None:
            return int(tensor.shape[0])  # torch.Tensor and np.ndarray both expose shape.
    raise ValueError("cannot infer PPO action dimension: missing actor output weight")


def infer_encoding_version(state_dict: Mapping[str, object]) -> int:
    """Infer V1/V2 from the actor output-layer weight rows in a checkpoint state_dict."""
    return encoding_version_for_action_dim(infer_action_dim(state_dict))


def infer_action_profile(checkpoint_or_state: Mapping[str, object]) -> str:
    """Resolve the action profile from checkpoint metadata when present, else action width.

    Legacy realprice checkpoints were trained with the 4-mode p2p head despite their
    market metadata, so fallback intentionally keys only off actor output width.
    """
    explicit = checkpoint_or_state.get("action_profile")
    if explicit is not None:
        return normalize_action_profile(str(explicit))
    if "state_dict" in checkpoint_or_state and isinstance(checkpoint_or_state["state_dict"], Mapping):
        state = checkpoint_or_state["state_dict"]  # type: ignore[assignment]
    else:
        state = checkpoint_or_state
    return action_profile_for_action_dim(infer_action_dim(state))


def obs_dim_for(version: int) -> int:
    if version == OBS_V1:
        return OBS_DIM_V1
    if version == OBS_V3:
        return OBS_DIM_V3
    raise ValueError(f"unsupported PPO observation version: {version}")


def obs_version_for_obs_dim(dim: int) -> int:
    if dim == OBS_DIM_V1:
        return OBS_V1
    if dim == OBS_DIM_V3:
        return OBS_V3
    raise ValueError(f"unsupported PPO observation dimension: {dim}")


def infer_obs_dim(state_dict: Mapping[str, object]) -> int:
    """Infer the observation width from the first trunk Linear in a checkpoint state_dict."""
    if "state_dict" in state_dict and isinstance(state_dict["state_dict"], Mapping):
        return infer_obs_dim(state_dict["state_dict"])  # type: ignore[arg-type]
    for key in ("trunk.0.weight", "net.0.weight"):
        tensor = state_dict.get(key)
        if tensor is not None:
            return int(tensor.shape[1])  # torch.Tensor and np.ndarray both expose shape.
    raise ValueError("cannot infer PPO obs_dim: missing first trunk weight")


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def _finite_clamped(value: float, lo: float = -5.0, hi: float = 5.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(out):
        return 0.0
    return max(lo, min(hi, out))


def _forecast_value(forecast: object | None, target: str, horizon: str) -> float | None:
    if forecast is None:
        return None
    series = getattr(forecast, target, None)
    if series is None:
        return None
    try:
        return float(series.by_horizon(horizon).value)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _forecast_solar_factor(forecast: object | None, horizon: str) -> float | None:
    if forecast is None:
        return None
    try:
        return float(forecast.solar_factor(horizon))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def encode_obs(ctx: AgentContext, valuation: ValuationSignal, *, obs_version: int = OBS_V1) -> np.ndarray:
    """AgentContext + ValuationSignal → fixed-width observation."""
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
    obs = np.array(
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
    if obs_version == OBS_V1:
        return obs
    if obs_version != OBS_V3:
        raise ValueError(f"unsupported PPO observation version: {obs_version}")
    forecast = ctx.forecast
    real_1h = _forecast_value(forecast, "price_real", "1h")
    real_12h = _forecast_value(forecast, "price_real", "12h")
    p2p_1h = _forecast_value(forecast, "price_p2p", "1h")
    p2p_12h = _forecast_value(forecast, "price_p2p", "12h")
    solar_1h = _forecast_solar_factor(forecast, "1h")
    solar_12h = _forecast_solar_factor(forecast, "12h")
    forecast_obs = np.array(
        [
            0.0 if real_1h is None else _finite_clamped((real_1h - mid) / pr),
            0.0 if real_12h is None else _finite_clamped((real_12h - mid) / pr),
            0.0 if p2p_1h is None else _finite_clamped((p2p_1h - mid) / max(mid, 1e-3)),
            0.0 if p2p_12h is None else _finite_clamped((p2p_12h - mid) / max(mid, 1e-3)),
            0.0 if solar_1h is None else _finite_clamped(solar_1h),
            0.0 if solar_12h is None else _finite_clamped(solar_12h),
        ],
        dtype=np.float32,
    )
    return np.concatenate([obs, forecast_obs]).astype(np.float32, copy=False)


def _logit(p: float) -> float:
    p = min(1.0 - 1e-3, max(1e-3, p))
    return max(-5.0, min(5.0, math.log(p / (1.0 - p))))


def _atanh_clamped(x: float) -> float:
    x = max(-1.0 + 1e-3, min(1.0 - 1e-3, x))
    return max(-5.0, min(5.0, math.atanh(x)))


def encode_action(
    action: StrategyAction,
    *,
    version: int = ENCODING_V1,
    modes: list[StrategyMode] | tuple[StrategyMode, ...] | None = None,
    market_mode: str | None = None,
    action_profile: str | None = None,
) -> np.ndarray:
    """StrategyAction → a raw policy vector that decode_action maps back to it — the
    supervised target for behavior cloning. Inverse of decode_action up to the squash
    clamps; modes outside the PPO set clone to NOOP."""
    mode_list = list(modes) if modes is not None else primitive_modes_for(market_mode, action_profile=action_profile)
    n_modes = len(mode_list)
    vec = np.full(action_dim(version, modes=mode_list), -1.0, dtype=np.float32)
    try:
        idx = mode_list.index(action.mode)
    except ValueError:
        idx = 0  # NOOP
    vec[idx] = 1.0
    p = vec[n_modes:]
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


def decode_action(
    vec: np.ndarray,
    *,
    version: int = ENCODING_V1,
    modes: list[StrategyMode] | tuple[StrategyMode, ...] | None = None,
    market_mode: str | None = None,
    action_profile: str | None = None,
) -> StrategyAction:
    """Raw policy vector (ACTION_DIM) → a bounded StrategyAction. The first N_MODES
    components are mode logits (argmax picks the primitive); the rest are squashed
    into their parameter ranges."""
    mode_list = list(modes) if modes is not None else primitive_modes_for(market_mode, action_profile=action_profile)
    n_modes = len(mode_list)
    vec = np.asarray(vec, dtype=np.float32).flatten()
    expected = action_dim(version, modes=mode_list)
    if vec.shape[0] != expected:
        raise ValueError(f"expected PPO action width {expected} for encoding V{version}, got {vec.shape[0]}")
    mode = mode_list[int(np.argmax(vec[:n_modes]))]
    p = vec[n_modes:]
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
