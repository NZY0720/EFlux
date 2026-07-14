import { useEffect, useRef, useState } from "react";

import { fetchArena } from "../api/client";
import type { ArenaPayload, MarketAgent } from "../api/types";
import { useMarket } from "./marketStream";

export interface PnlPoint {
  t: number; // wall-clock ms
  pnl: number; // USD
}

export interface StrategyPnl {
  /** Latest agent snapshot, newest poll. */
  agents: MarketAgent[];
  /** Per-agent cumulative-PnL time series, accumulated client-side. */
  history: Record<string, PnlPoint[]>;
  /** Arena evidence contract from the server. Null only before the first response. */
  arena: Omit<ArenaPayload, "agents"> | null;
}

// Retain the full session's PnL history (~28h at a 2s cadence) so the equity
// curves can zoom back to the start; bounded only as a memory safety net.
const MAX_POINTS = 50_000;

/**
 * Polls GET /market/arena and accumulates each agent's PnL over time on the
 * client (the backend exposes a point-in-time PnL but no history). Powers the
 * Real-Time dashboard's strategy leaderboard and equity curves.
 */
export function useStrategyPnl(intervalMs = 2000): StrategyPnl {
  const { restartedAt } = useMarket();
  const [agents, setAgents] = useState<MarketAgent[]>([]);
  const [history, setHistory] = useState<Record<string, PnlPoint[]>>({});
  const [arena, setArena] = useState<Omit<ArenaPayload, "agents"> | null>(null);
  const histRef = useRef<Record<string, PnlPoint[]>>({});

  useEffect(() => {
    if (restartedAt === null) return;
    histRef.current = {};
    setHistory({});
  }, [restartedAt]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await fetchArena();
        if (cancelled) return;
        const now = Date.now();
        const next: Record<string, PnlPoint[]> = { ...histRef.current };
        for (const a of data.agents) {
          const pnl = Number(a.pnl);
          if (!Number.isFinite(pnl)) continue;
          const prev = next[a.name] ?? [];
          next[a.name] = [...prev, { t: now, pnl }].slice(-MAX_POINTS);
        }
        histRef.current = next;
        setHistory(next);
        setAgents(data.agents);
        setArena({ min_trades: data.min_trades, min_observation_min: data.min_observation_min });
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
  }, [intervalMs, restartedAt]);

  return { agents, history, arena };
}
