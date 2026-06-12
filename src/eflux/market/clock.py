"""Simulation clock with adjustable speed (1x / 10x / 100x / replay).

The clock decouples sim time from wall time. Speed = 10 means 1 wall second advances
10 sim seconds. Speed=1.0 is the only mode that allows external SDK agents to interact;
faster modes are for training and offline replay.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass
class SimClock:
    """Tracks sim time vs wall time. Pure data — no async."""

    sim_epoch: datetime
    wall_epoch: datetime = field(default_factory=lambda: datetime.now(UTC))
    speed: float = 1.0  # sim seconds per wall second

    def now_sim(self) -> datetime:
        wall_elapsed = (datetime.now(UTC) - self.wall_epoch).total_seconds()
        return self.sim_epoch + timedelta(seconds=wall_elapsed * self.speed)

    @property
    def is_realtime(self) -> bool:
        return self.speed == 1.0


class RollingClock:
    """Async clock that fires periodic ticks. No reset semantics — runs forever."""

    def __init__(
        self,
        sim_epoch: datetime,
        speed: float = 1.0,
        tick_sim_sec: float = 1.0,
    ):
        if speed not in (1.0, 10.0, 100.0):
            raise ValueError(f"speed must be 1.0, 10.0, or 100.0; got {speed}")
        self.clock = SimClock(sim_epoch=sim_epoch, speed=speed)
        self.tick_sim_sec = tick_sim_sec
        self.tick_no = 0
        self._stop = asyncio.Event()

    @property
    def speed(self) -> float:
        return self.clock.speed

    @property
    def is_realtime(self) -> bool:
        return self.clock.is_realtime

    def now_sim(self) -> datetime:
        return self.clock.now_sim()

    def set_speed(self, speed: float) -> None:
        """Change speed at runtime. Rebases the epochs so sim time is continuous
        across the change — only the rate at which it advances jumps."""
        if speed not in (1.0, 10.0, 100.0):
            raise ValueError(f"speed must be 1.0, 10.0, or 100.0; got {speed}")
        if speed == self.clock.speed:
            return
        self.clock.sim_epoch = self.clock.now_sim()
        self.clock.wall_epoch = datetime.now(UTC)
        self.clock.speed = speed

    def stop(self) -> None:
        self._stop.set()

    async def ticks(self):
        """Async generator yielding (tick_no, sim_ts) on every tick."""
        while not self._stop.is_set():
            self.tick_no += 1
            yield self.tick_no, self.now_sim()
            # Recomputed every iteration so set_speed() takes effect mid-run.
            wall_interval = self.tick_sim_sec / self.speed
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wall_interval)
            except TimeoutError:
                continue
