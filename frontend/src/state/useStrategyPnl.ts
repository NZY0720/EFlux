import { useEffect, useRef, useState } from "react";

import { fetchMarketAgents } from "../api/client";
import type { MarketAgent } from "../api/types";

export interface PnlPoint {
  t: number; // wall-clock ms
  pnl: number; // USD
}

export interface StrategyPnl {
  /** Latest agent snapshot, newest poll. */
  agents: MarketAgent[];
  /** Per-agent cumulative-PnL time series, accumulated client-side. */
  history: Record<string, PnlPoint[]>;
}

// Retain the full session's PnL history (~28h at a 2s cadence) so the equity
// curves can zoom back to the start; bounded only as a memory safety net.
const MAX_POINTS = 50_000;

/**
 * Polls GET /market/agents and accumulates each agent's PnL over time on the
 * client (the backend exposes a point-in-time PnL but no history). Powers the
 * Real-Time dashboard's strategy leaderboard and equity curves.
 */
export function useStrategyPnl(intervalMs = 2000): StrategyPnl {
  const [agents, setAgents] = useState<MarketAgent[]>([]);
  const [history, setHistory] = useState<Record<string, PnlPoint[]>>({});
  const histRef = useRef<Record<string, PnlPoint[]>>({});

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await fetchMarketAgents();
        if (cancelled) return;
        const now = Date.now();
        const next: Record<string, PnlPoint[]> = { ...histRef.current };
        for (const a of data) {
          const pnl = Number(a.pnl);
          if (!Number.isFinite(pnl)) continue;
          const prev = next[a.name] ?? [];
          next[a.name] = [...prev, { t: now, pnl }].slice(-MAX_POINTS);
        }
        histRef.current = next;
        setHistory(next);
        setAgents(data);
      } catch {
        /* transient backend hiccup — keep the last good data */
      }
    };
    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs]);

  return { agents, history };
}
