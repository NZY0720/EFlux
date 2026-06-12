"""Unit tests for the simulation clock."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from eflux.market.clock import RollingClock, SimClock


def test_simclock_realtime_advances_with_wall():
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    c = SimClock(sim_epoch=epoch, wall_epoch=datetime.now(UTC), speed=1.0)
    assert c.is_realtime
    time.sleep(0.05)
    elapsed = (c.now_sim() - epoch).total_seconds()
    assert 0.04 < elapsed < 0.5  # gives slack for CI variability


def test_simclock_speed_multiplier_scales_sim_time():
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    c = SimClock(sim_epoch=epoch, wall_epoch=datetime.now(UTC), speed=100.0)
    assert not c.is_realtime
    time.sleep(0.05)
    elapsed = (c.now_sim() - epoch).total_seconds()
    # 0.05s wall * 100x = 5s sim (with slack)
    assert elapsed > 4.0


def test_rolling_clock_rejects_unsupported_speed():
    with pytest.raises(ValueError):
        RollingClock(sim_epoch=datetime.now(UTC), speed=2.5)


@pytest.mark.asyncio
async def test_rolling_clock_emits_ticks_at_interval():
    clock = RollingClock(sim_epoch=datetime.now(UTC), speed=100.0, tick_sim_sec=1.0)
    # At 100x speed, wall_interval = 1/100 = 0.01s. Collect 3 ticks.
    collected = []
    start = time.monotonic()

    async def collect():
        async for tick_no, sim_ts in clock.ticks():
            collected.append((tick_no, sim_ts))
            if len(collected) >= 3:
                clock.stop()
                break

    await asyncio.wait_for(collect(), timeout=2.0)
    elapsed = time.monotonic() - start
    assert [t[0] for t in collected] == [1, 2, 3]
    # Should be roughly 3 * 0.01s = 0.03s; allow large slack but cap below 1s
    # (would be 3s if speed multiplier wasn't applied to wall interval).
    assert elapsed < 1.0


def test_rolling_clock_is_realtime_property_tracks_speed():
    assert RollingClock(sim_epoch=datetime.now(UTC), speed=1.0).is_realtime
    assert not RollingClock(sim_epoch=datetime.now(UTC), speed=10.0).is_realtime


def test_set_speed_rejects_unsupported_value():
    clock = RollingClock(sim_epoch=datetime.now(UTC), speed=1.0)
    with pytest.raises(ValueError):
        clock.set_speed(2.5)
    assert clock.speed == 1.0


def test_set_speed_keeps_sim_time_continuous():
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    clock = RollingClock(sim_epoch=epoch, speed=1.0)
    # Pretend 60 wall seconds have passed at 1x → sim time is epoch+60s.
    clock.clock.wall_epoch = datetime.now(UTC) - timedelta(seconds=60)
    before = clock.now_sim()

    clock.set_speed(100.0)
    after = clock.now_sim()

    assert clock.speed == 100.0
    # Rebase must not jump sim time: the 60 already-elapsed wall seconds must
    # not be retroactively re-scaled by the new 100x multiplier.
    assert abs((after - before).total_seconds()) < 1.0


def test_set_speed_same_value_is_noop():
    clock = RollingClock(sim_epoch=datetime.now(UTC), speed=10.0)
    epoch_before = clock.clock.sim_epoch
    clock.set_speed(10.0)
    assert clock.clock.sim_epoch == epoch_before


@pytest.mark.asyncio
async def test_ticks_interval_follows_runtime_speed_change():
    clock = RollingClock(sim_epoch=datetime.now(UTC), speed=1.0, tick_sim_sec=1.0)
    collected = []
    start = time.monotonic()

    async def collect():
        async for tick_no, _sim_ts in clock.ticks():
            collected.append(tick_no)
            # After the first tick, jump to 100x: remaining ticks should fire
            # at ~0.01s wall intervals instead of 1s.
            if tick_no == 1:
                clock.set_speed(100.0)
            if len(collected) >= 4:
                clock.stop()
                break

    await asyncio.wait_for(collect(), timeout=5.0)
    elapsed = time.monotonic() - start
    assert collected == [1, 2, 3, 4]
    # First interval may take up to ~1s (set during yield, before the sleep);
    # at the old speed the remaining three would add 3 more seconds.
    assert elapsed < 2.5
