import { useEffect, useState } from "react";

import { fetchLeaderboardHistory } from "../api/client";
import type { PnlPoint } from "./useStrategyPnl";

/**
 * Server-backed equity curves from GET /leaderboard/history — unlike useStrategyPnl's
 * client-side accumulation, these survive page refreshes and backend restarts. Feeds
 * the same PnlPoint shape the EquityCurves component consumes.
 *
 * identities: leaderboard identity strings ("name:<name>" | "managed:<def_id>").
 */
export function useServerEquity(
  identities: string[],
  sessionId?: number,
  intervalMs = 10_000,
): Record<string, PnlPoint[]> {
  const [history, setHistory] = useState<Record<string, PnlPoint[]>>({});
  // Key the effect on content, not array identity, so callers can pass literals.
  const key = identities.join("\u0000");

  useEffect(() => {
    const ids = key ? key.split("\u0000") : [];
    if (ids.length === 0) {
      setHistory({});
      return;
    }
    setHistory({});
    let cancelled = false;
    const tick = async () => {
      const next: Record<string, PnlPoint[]> = {};
      await Promise.all(
        ids.map(async (identity) => {
          const [kind, ...rest] = identity.split(":");
          const value = rest.join(":");
          try {
            const h = await fetchLeaderboardHistory({
              ...(kind === "managed"
                ? { managed_def_id: Number(value) }
                : { name: value }),
              session_id: sessionId,
            });
            const label = kind === "managed" ? `managed #${value}` : value;
            next[label] = h.points.map((p) => ({
              t: Date.parse(p.wall_ts),
              pnl: Number(p.pnl_usd),
            }));
          } catch {
            /* identity may have no snapshots yet — skip, keep the rest */
          }
        }),
      );
      if (!cancelled) setHistory(next);
    };
    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [key, sessionId, intervalMs]);

  return history;
}
