"""Per-agent persistent memory — append-only JSONL of closed hint windows.

Each record attributes one reflection's hints to the PnL/trade outcome observed
over the following window, so the next prompt can say "you set price_adjust +5%
and PnL over the next window was X". Records survive restarts by construction;
an agent without a path keeps the same record buffer purely in memory.

Record shape (v=1):
  {"v": 1, "ts": "<wall iso>", "sim_ts": "<sim iso>", "tick": 4321,
   "hints": {"price_adjust": 0.05, "qty_scale": 1.2},
   "rationale": "bid up into evening peak",
   "lesson": "evening deficits clear faster bidding ~5% over ref",
   "window": {"ticks": 60, "pnl": 1.84, "trades": 3, "soc_end": 0.41}}
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)


def slug(name: str) -> str:
    """Filesystem-safe agent name for the memory filename."""
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "agent"


class AgentMemory:
    """Bounded record buffer with optional JSONL persistence."""

    def __init__(self, path: Path | str | None, max_loaded: int = 20) -> None:
        self.path = Path(path) if path is not None else None
        self.records: deque[dict] = deque(maxlen=max_loaded)

    def load(self) -> int:
        """Read the tail of the JSONL file into the buffer; skip corrupt lines.

        Returns the number of records loaded. Missing file (fresh agent) or an
        unreadable one is not an error — the agent just starts without history.
        """
        if self.path is None or not self.path.exists():
            return 0
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            log.warning("Agent memory unreadable: %s — starting empty", self.path)
            return 0
        loaded = 0
        for line in lines[-(self.records.maxlen or 20):]:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn write / manual edit — drop the line, keep the rest
            if isinstance(record, dict):
                self.records.append(record)
                loaded += 1
        return loaded

    def append(self, record: dict) -> None:
        """Add a record to the buffer and flush-append it to disk (if persistent)."""
        self.records.append(record)
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            log.warning("Agent memory write failed: %s (record kept in memory)", self.path)

    def last(self, k: int) -> list[dict]:
        return list(self.records)[-k:]
