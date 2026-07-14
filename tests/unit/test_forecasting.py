from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from eflux.forecasting.models import (
    AnchorForecast,
    HorizonModel,
    HourlyEwmaProfile,
    SeasonalPersistence,
    WeatherForecaster,
)
from eflux.forecasting.schema import HORIZON_TIMEDELTAS, HORIZONS, ForecastBundle
from eflux.forecasting.service import ForecastService


def _hours(start: datetime, count: int) -> list[datetime]:
    return [start + timedelta(hours=i) for i in range(count)]


def _seasonal_series(times: list[datetime]) -> list[float]:
    rng = np.random.default_rng(123)
    values: list[float] = []
    for i, ts in enumerate(times):
        hour = ts.hour + ts.minute / 60.0
        daily = 22.0 * np.sin(2.0 * np.pi * hour / 24.0)
        harmonic = 7.0 * np.cos(4.0 * np.pi * hour / 24.0)
        trend = 0.025 * i
        noise = rng.normal(0.0, 0.15)
        values.append(float(55.0 + daily + harmonic + trend + noise))
    return values


def test_warm_started_online_forecaster_beats_naive_persistence_on_tail():
    times = _hours(datetime(2026, 1, 1, 0, 0), 12 * 24)
    values = _seasonal_series(times)
    split = 8 * 24

    service = ForecastService()
    service.warm_start(series={"price_real": zip(times[:split], values[:split], strict=True)})

    forecast_errors: list[float] = []
    naive_errors: list[float] = []
    for i in range(split, len(times) - 1):
        bundle = service.refresh(times[i])
        forecast = bundle.price_real.by_horizon("1h").value
        actual = values[i + 1]
        forecast_errors.append(abs(forecast - actual))
        naive_errors.append(abs(values[i] - actual))
        service.observe(times[i], price_real=values[i])

    assert np.mean(forecast_errors) < np.mean(naive_errors)


def test_observe_refresh_returns_finite_populated_bundle():
    service = ForecastService()
    start = datetime(2026, 3, 1, 0, 0)
    for i, ts in enumerate(_hours(start, 36)):
        service.observe(
            ts,
            price_real=50.0 + i,
            price_p2p=45.0 + 0.5 * i,
            ghi=max(0.0, 700.0 * np.sin(np.pi * ts.hour / 24.0)),
            temp_air=20.0 + np.sin(i),
            wind_speed=4.0 + 0.1 * i,
        )

    bundle = service.refresh(start + timedelta(hours=36))
    for target_name in ("price_real", "price_p2p", "ghi", "temp_air", "wind_speed"):
        target = getattr(bundle, target_name)
        for horizon in HORIZONS:
            point = target.by_horizon(horizon)
            assert np.isfinite(point.value)
            assert point.stderr is None or np.isfinite(point.stderr)


def test_refresh_history_records_forecasts_and_latest_realized_values():
    service = ForecastService()
    ts = datetime(2026, 3, 1, 0, 0)
    service.observe(ts, price_real=51.0, ghi=300.0)

    service.refresh(ts + timedelta(minutes=1))
    history = service.history()

    assert len(history) == 1
    record = history[0]
    assert record["as_of"] == (ts + timedelta(minutes=1)).isoformat()
    assert set(record["forecasts"]) == {"price_real", "price_p2p", "ghi", "temp_air", "wind_speed"}
    assert set(record["forecasts"]["price_real"]) == {"5m", "1h", "12h"}
    assert record["realized"]["price_real"] == 51.0
    assert record["realized"]["ghi"] == 300.0
    assert record["realized"]["price_p2p"] is None

    filtered = service.history(limit=1, target="price_real")
    assert filtered == [
        {
            "as_of": record["as_of"],
            "forecasts": {"price_real": record["forecasts"]["price_real"]},
            "realized": {"price_real": 51.0},
        }
    ]


def test_observation_count_and_is_warm_track_price_observations():
    service = ForecastService()

    assert service.observation_count() == 0
    assert service.observation_count("price_real", "price_p2p") == 0
    assert service.is_warm is False

    start = datetime(2026, 3, 1, 0, 0)
    samples = _hours(start, 3)
    service.warm_start(
        series={
            "price_real": ((ts, 50.0 + i) for i, ts in enumerate(samples)),
            "price_p2p": ((ts, 45.0 + i) for i, ts in enumerate(samples)),
        }
    )

    assert service.observation_count("price_real", "price_p2p") == 6
    assert service.is_warm is True


def test_weather_only_observations_do_not_make_service_warm():
    service = ForecastService()
    start = datetime(2026, 3, 1, 0, 0)

    service.warm_start(series={"ghi": ((ts, 100.0) for ts in _hours(start, 3))})

    assert service.observation_count() == 3
    assert service.observation_count("price_real", "price_p2p") == 0
    assert service.is_warm is False


def test_seasonal_persistence_cold_start_is_sane():
    model = SeasonalPersistence()
    ts = datetime(2026, 4, 1, 3, 0)

    assert model.predict(ts) == 0.0
    model.observe(ts, 12.5)
    model.observe(ts + timedelta(hours=1), 13.5)

    assert model.predict(ts + timedelta(days=1)) == 12.5
    assert model.predict(ts + timedelta(hours=10)) == 13.5


def test_weather_forecaster_uses_nwp_base_plus_learned_bias():
    def nwp_lookup(ts: datetime) -> float:
        return 100.0 + ts.hour

    model = WeatherForecaster(nwp_lookup=nwp_lookup)
    start = datetime(2026, 5, 1, 0, 0)
    before = model.predict(start)["1h"].value

    for ts in _hours(start, 8):
        model.observe(ts, nwp_lookup(ts) + 7.0)

    after = model.predict(start + timedelta(hours=8))["1h"].value
    expected = nwp_lookup(start + timedelta(hours=9)) + 7.0

    assert abs(after - expected) < abs(before - expected)
    assert abs(after - expected) < 0.5


def test_feature_lag_lookback_is_older_for_12h_than_5m():
    model = HorizonModel()
    start = datetime(2026, 5, 1, 0, 0)
    for minute in range(2 * 24 * 60 + 1):
        model.observe(start + timedelta(minutes=minute), float(minute))

    origin = start + timedelta(days=2)
    five_minute_features = model._features(origin, HORIZON_TIMEDELTAS["5m"])
    twelve_hour_features = model._features(origin, HORIZON_TIMEDELTAS["12h"])

    # v1 layout: [1, hour harmonics x4, dow x2, last, lag, trend, ramp] — lag sits at index 8.
    assert five_minute_features.shape == (11,)
    assert twelve_hour_features.shape == (11,)
    assert five_minute_features[8] == float(2 * 24 * 60 - 5)
    assert twelve_hour_features[8] == float(24 * 60)
    assert twelve_hour_features[8] < five_minute_features[8]


def test_forecast_bundle_empty_is_neutral_and_json_serializable():
    bundle = ForecastBundle.empty(as_of=datetime(2026, 1, 1, 0, 0))

    assert bundle.solar_factor("1h") == 0.0
    for target_name in ("price_real", "price_p2p", "ghi", "temp_air", "wind_speed"):
        target = getattr(bundle, target_name)
        for horizon in HORIZONS:
            point = target.by_horizon(horizon)
            assert point.value == 0.0
            assert point.stderr == 0.0

    json.dumps(bundle.to_dict())


def test_save_load_round_trip_reproduces_predictions(tmp_path, monkeypatch):
    from eflux.config import get_settings

    service = ForecastService()
    start = datetime(2026, 6, 1, 0, 0)
    samples = _hours(start, 72)
    values = _seasonal_series(samples)
    service.warm_start(
        series={
            "price_real": zip(samples, values, strict=True),
            "price_p2p": ((ts, value * 0.9) for ts, value in zip(samples, values, strict=True)),
            "ghi": ((ts, max(0.0, value * 10.0)) for ts, value in zip(samples, values, strict=True)),
            "temp_air": ((ts, 15.0 + value * 0.02) for ts, value in zip(samples, values, strict=True)),
            "wind_speed": ((ts, 2.0 + value * 0.01) for ts, value in zip(samples, values, strict=True)),
        }
    )
    sim_ts = start + timedelta(hours=72)
    before = service.refresh(sim_ts).to_dict()
    before_history = service.history()

    path = tmp_path / "forecast_state.json"
    service.save(path)

    # Default: session-scoped chart — models reproduce, history starts clean.
    loaded = ForecastService.load(path)
    assert loaded.history() == []
    after = loaded.refresh(sim_ts).to_dict()
    assert after == before

    # Continuity mode restores the persisted history verbatim.
    monkeypatch.setenv("EFLUX_FORECAST_HISTORY_RESET_ON_BOOT", "false")
    get_settings.cache_clear()
    try:
        kept = ForecastService.load(path)
        assert kept.history() == before_history
    finally:
        get_settings.cache_clear()


def test_load_rejects_pre_timezone_canonicalization_state(tmp_path):
    service = ForecastService()
    start = datetime(2026, 6, 1, 0, 0)
    service.observe(start, price_real=42.0)
    service.refresh(start)
    path = tmp_path / "state.json"
    service.save(path)
    state = json.loads(path.read_text(encoding="utf-8"))
    state["model_version"] = "online-rls-v0"
    path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ValueError, match="incompatible forecast state"):
        ForecastService.load(path)


def test_cached_price_windows_parses_sanitized_names_and_sorts(tmp_path):
    from datetime import date

    from eflux.agents.ppo.training_data import cached_price_windows

    node = "TH_SP15/GEN-APND"  # "/" is sanitized to "_" in cache filenames
    for name in (
        "lmp_TH_SP15_GEN-APND_2026-06-05_2026-07-05.parquet",
        "lmp_TH_SP15_GEN-APND_2026-06-08_2026-07-08.parquet",
        "lmp_TH_SP15_GEN-APND_not-a-window.parquet",
        "refmean_TH_SP15_GEN-APND_2026-06-05_2026-07-05.txt",
    ):
        (tmp_path / name).touch()

    windows = cached_price_windows(node=node, cache_dir=tmp_path)

    assert windows == [
        (date(2026, 6, 8), date(2026, 7, 8)),
        (date(2026, 6, 5), date(2026, 7, 5)),
    ]
    assert cached_price_windows(node="OTHER_NODE", cache_dir=tmp_path) == []
    assert cached_price_windows(node=node, cache_dir=tmp_path / "missing") == []


def test_state_rejects_non_v1_feature_dimension():
    model = HorizonModel()
    start = datetime(2026, 5, 1, 0, 0)
    for hour in range(72):
        model.observe(start + timedelta(hours=hour), 40.0 + (hour % 24))
    state = model.to_state()
    # A non-V1 feature width must fail closed instead of being silently upgraded.
    for lin in state["linear"].values():
        lin["n_features"] = 8
        lin["coef"] = [0.0] * 8
        lin["P"] = [[float(i == j) for j in range(8)] for i in range(8)]

    with pytest.raises(ValueError, match="feature schema does not match V1"):
        HorizonModel.from_state(state)


def test_price_anchor_blends_dam_base_with_online_residual():
    service = ForecastService(
        nwp={"price_real": lambda ts: 42.0, "price_p2p": lambda ts: 42.0}
    )
    assert isinstance(service.models["price_real"], WeatherForecaster)
    assert isinstance(service.models["price_p2p"], WeatherForecaster)

    start = datetime(2026, 6, 1, 0, 0)
    for i in range(12):
        service.observe(start + timedelta(minutes=i), price_real=45.0, price_p2p=45.0)
    bundle = service.refresh(start + timedelta(minutes=12))

    # The near horizon remains current-price dominated; by 12h the DAM anchor
    # dominates and the short-lived residual correction has decayed.
    assert bundle.price_real.h1h.value == pytest.approx(45.0, abs=0.5)
    assert 42.0 < bundle.price_real.h12h.value < 45.0
    assert bundle.price_p2p.h1h.value == pytest.approx(45.0, abs=0.5)


def test_price_anchor_clamps_residual_and_keeps_five_minutes_at_persistence(monkeypatch):
    monkeypatch.setenv("EFLUX_FORECAST_DAM_RESIDUAL_LIMIT", "20")
    model = WeatherForecaster(
        nwp_lookup=lambda ts: AnchorForecast(100.0, "dam", "curve-a"),
        output_bounds=(-50.0, 250.0),
        residual_limit=20.0,
        use_horizon_blend=True,
    )
    start = datetime(2026, 6, 1, 0, 0)
    model.observe(start, 200.0)

    points = model.predict(start)

    assert model.residual_ewma == pytest.approx(20.0)
    assert points["5m"].value == pytest.approx(200.0)
    assert points["5m"].provenance == "persistence"
    assert -50.0 <= points["12h"].value <= 250.0


def test_price_anchor_resets_residual_when_curve_source_changes():
    source = {"id": "curve-a"}

    def lookup(ts):
        return AnchorForecast(40.0, "dam", source["id"])

    model = WeatherForecaster(
        nwp_lookup=lookup,
        residual_limit=20.0,
        use_horizon_blend=True,
    )
    start = datetime(2026, 6, 1, 0, 0)
    model.observe(start, 55.0)
    assert model.residual_ewma == pytest.approx(15.0)

    source["id"] = "curve-b"
    model.predict(start + timedelta(hours=1))

    assert model.residual_ewma == 0.0
    assert model.n_residuals == 0


def test_price_blend_converges_to_current_price_near_now():
    model = WeatherForecaster(
        nwp_lookup=lambda ts: AnchorForecast(42.0, "dam", "curve-a"),
        output_bounds=(-50.0, 250.0),
        residual_limit=20.0,
        dam_blend_max=0.85,
        dam_blend_full_hours=6.0,
        residual_decay_hours=4.0,
        use_horizon_blend=True,
    )
    start = datetime(2026, 6, 1, 0, 0)
    model.observe(start, 45.0)

    points = model.predict(start)

    assert points["5m"].value == pytest.approx(45.0)
    assert abs(points["1h"].value - 45.0) < 1.0


def test_load_upgrades_legacy_price_state_when_anchor_available(tmp_path):
    service = ForecastService()  # no anchor: plain HorizonModel price models
    start = datetime(2026, 6, 1, 0, 0)
    samples = [(start + timedelta(hours=i), 50.0 + (i % 5)) for i in range(48)]
    service.warm_start(series={"price_real": samples, "price_p2p": samples})
    path = tmp_path / "state.json"
    service.save(path)

    plain = ForecastService.load(path)
    assert not isinstance(plain.models["price_real"], WeatherForecaster)

    anchored = ForecastService.load(path, nwp={"price_real": lambda ts: 48.0})
    model = anchored.models["price_real"]
    assert isinstance(model, WeatherForecaster)
    assert anchored.observation_count("price_real") == 48
    assert anchored.is_warm
    bundle = anchored.refresh(start + timedelta(hours=48))
    assert np.isfinite(bundle.price_real.h1h.value)
    # price_p2p had no lookup, so it stays un-anchored.
    assert not isinstance(anchored.models["price_p2p"], WeatherForecaster)

    # An anchored save round-trips through the anchored loader.
    path2 = tmp_path / "state2.json"
    anchored.save(path2)
    again = ForecastService.load(path2, nwp={"price_real": lambda ts: 48.0})
    assert isinstance(again.models["price_real"], WeatherForecaster)
    assert again.observation_count("price_real") == 48


def test_hourly_ewma_profile_gates_cold_buckets_and_tracks_market_hour():
    profile = HourlyEwmaProfile(alpha=0.5, min_obs=3)
    ts = datetime(2026, 7, 10, 14, 5)
    for value in (10.0, 20.0, 30.0):
        profile.observe(ts, value)

    # EWMA at alpha=0.5: 10 -> 15 -> 22.5.
    assert profile.predict(ts) == pytest.approx(22.5)
    # The same instant expressed in UTC hits the same market-hour bucket (PDT).
    assert profile.predict(datetime(2026, 7, 10, 21, 5, tzinfo=UTC)) == pytest.approx(22.5)
    # Other hour buckets stay cold.
    assert profile.predict(ts + timedelta(hours=1)) is None
    # Below min_obs a bucket is unavailable even after seeing data.
    other = ts + timedelta(hours=2)
    profile.observe(other, 40.0)
    assert profile.predict(other) is None


def test_hourly_ewma_profile_state_round_trip():
    profile = HourlyEwmaProfile(alpha=0.2, min_obs=2)
    ts = datetime(2026, 7, 10, 9, 0)
    profile.observe(ts, 18.0)
    profile.observe(ts, 22.0)

    restored = HourlyEwmaProfile.from_state(json.loads(json.dumps(profile.to_state())))

    assert restored.predict(ts) == pytest.approx(profile.predict(ts))
    assert restored.predict(ts + timedelta(hours=3)) is None
    assert restored.alpha == pytest.approx(0.2)
    assert restored.min_obs == 2


def test_p2p_cold_start_yields_explicit_degraded_forecast():
    model = WeatherForecaster(
        nwp_lookup=lambda ts: AnchorForecast(None, "p2p_cold_start", "p2p-profile-v1"),
        output_bounds=(-50.0, 250.0),
        residual_limit=20.0,
        use_horizon_blend=True,
    )
    now = datetime(2026, 7, 10, 13, 0)
    for i in range(4):
        model.observe(now - timedelta(minutes=5 * (4 - i)), 17.0)

    points = model.predict(now)

    assert points["5m"].provenance == "persistence"
    assert points["1h"].provenance == "p2p_cold_start"
    assert points["12h"].provenance == "p2p_cold_start"
    for horizon in HORIZONS:
        assert -50.0 <= points[horizon].value <= 250.0


def test_p2p_profile_anchor_pulls_long_horizons_toward_market_level():
    model = WeatherForecaster(
        nwp_lookup=lambda ts: AnchorForecast(18.0, "p2p_profile", "p2p-profile-v1"),
        output_bounds=(-50.0, 250.0),
        residual_limit=20.0,
        use_horizon_blend=True,
    )
    now = datetime(2026, 7, 10, 13, 0)
    for i in range(30):
        model.observe(now - timedelta(minutes=5 * (30 - i)), 30.0)

    points = model.predict(now)

    assert points["5m"].value == pytest.approx(30.0)
    assert points["12h"].provenance == "p2p_profile"
    # 12h leans on the own-market anchor (residual decayed), not the last trade.
    assert points["12h"].value < 25.0
    # A stable profile source never resets the residual estimator.
    assert model.n_residuals == 30


def test_profile_prior_seeds_only_cold_buckets_and_is_labeled():
    profile = HourlyEwmaProfile(alpha=0.5, min_obs=3)
    warm_ts = datetime(2026, 7, 11, 8, 0)
    for _ in range(3):
        profile.observe(warm_ts, 20.0)

    seeded = profile.seed_prior({8: 99.0, 9: 30.0, 10: 35.0})

    assert seeded == 2  # hour 8 already has real data — never overwritten
    assert profile.predict(warm_ts) == pytest.approx(20.0)
    assert profile.predict(datetime(2026, 7, 11, 9, 0)) == pytest.approx(30.0)
    assert profile.is_prior(datetime(2026, 7, 11, 9, 0)) is True
    assert profile.is_prior(warm_ts) is False
    assert profile.has_real_data() is True


def test_first_real_print_replaces_prior_and_reearns_the_gate():
    profile = HourlyEwmaProfile(alpha=0.05, min_obs=5)
    profile.seed_prior({14: 40.0})
    ts = datetime(2026, 7, 11, 14, 30)

    profile.observe(ts, 18.0)

    # Replacement, not EWMA dilution — but a single print can't rule the hour:
    # the bucket drops back below min_obs until real observations accumulate.
    assert profile.is_prior(ts) is False
    assert profile.predict(ts) is None
    for _ in range(4):
        profile.observe(ts, 18.0)
    assert profile.predict(ts) == pytest.approx(18.0)


def test_profile_prior_survives_state_round_trip():
    profile = HourlyEwmaProfile(alpha=0.1, min_obs=4)
    profile.seed_prior({6: 25.0, 7: 28.0})

    restored = HourlyEwmaProfile.from_state(json.loads(json.dumps(profile.to_state())))

    assert restored.prior_hours == {6, 7}
    assert restored.predict(datetime(2026, 7, 11, 6, 0)) == pytest.approx(25.0)
    assert restored.has_real_data() is False


def test_load_resets_session_history_by_default_but_keeps_models(tmp_path, monkeypatch):
    from eflux.config import get_settings

    service = ForecastService()
    start = datetime(2026, 7, 1, 0, 0)
    for i in range(6):
        service.observe(start + timedelta(minutes=i), price_real=40.0 + i, price_p2p=18.0 + 0.1 * i)
    service.refresh(start + timedelta(minutes=6))
    path = tmp_path / "state.json"
    service.save(path)

    restored = ForecastService.load(path)

    # Models restore warm; the session-scoped chart surface starts clean.
    assert restored.observation_count("price_real") == 6
    assert restored.history() == []
    assert restored.latest.model_version == "empty"

    monkeypatch.setenv("EFLUX_FORECAST_HISTORY_RESET_ON_BOOT", "false")
    get_settings.cache_clear()
    try:
        kept = ForecastService.load(path)
        assert len(kept.history()) == 1
        assert kept.latest.model_version != "empty"
    finally:
        get_settings.cache_clear()
